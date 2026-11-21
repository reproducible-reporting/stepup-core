# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Asynchronous and synchronous RPC clients.

The clients that talk to a server over a socket,
`SocketAsyncRPCClient` and `SocketSyncRPCClient`,
follow the same connecting and closing contract,
which each of them implements on its own because one is asynchronous and the other is not:

- The connection is opened by the first call, not when the client is created.
- A client that was never used has no connection to close and sends no close message.
- Closing an already closed client does nothing,
  so an explicit `close()` inside a `with` block is allowed.
- A closed client cannot be used anymore and does not reconnect for a later call.
- The close message is a courtesy to the server, not a requirement.
  A server that is already gone, e.g. a director that has just accepted a shutdown,
  cannot be told that the conversation is over,
  which is no reason for `close()` to raise.
  Ending the connection conveys the same thing.
"""

import asyncio
import contextlib
import logging
import os
import pickle
import socket
import sys
from functools import cache, partial
from typing import Any, NoReturn

import attrs

from ..exceptions import ConfigError, RPCClientUnusableError, RPCError
from ..utils import is_debug
from .protocol import (
    RemoteFailure,
    RPCCall,
    SocketReader,
    encode_body,
    iter_stream_messages,
    recv_socket_message,
    send_socket_message,
    send_stream_message,
    suppress_peer_gone,
)
from .transport import read_endpoint, set_nodelay

__all__ = (
    "NO_RPC_TIMEOUT",
    "BaseAsyncRPCClient",
    "BaseSyncRPCClient",
    "DummyAsyncRPCClient",
    "DummySyncRPCClient",
    "SocketAsyncRPCClient",
    "SocketSyncRPCClient",
)

logger = logging.getLogger(__name__)


#
# Shared RPC client code
#


def _decode_response(
    body: bytes | None, call: RPCCall, *, server_log_description: str | None
) -> Any:
    """Turn the body of an RPC response into the result of the call, or raise.

    Unlike `_decode_request`, this is more than the inverse of the encoding step:
    a response that describes a failure comes back out as the exception it describes.

    Parameters
    ----------
    body
        The body of the response,
        or `None` when the server could not send the reply,
        see the wire format at the top of this module.
    call
        The call that this response answers, only used to describe it in an error message.
    server_log_description
        See `_SocketClientState.server_log_description`.

    Returns
    -------
    result
        Whatever the remote procedure returned.

    Raises
    ------
    RPCError
        When the server could not send the reply,
        when the response is not the response of an RPC server,
        or when the call failed on the server, see `_raise_remote_error`.
    """
    if body is None:
        # A `RemoteFailure` does not describe this failure:
        # the only thing that reaches the client is that no reply is coming for this call.
        # The reason stayed behind on the server, so the message says where to find it.
        where = (
            "the log of the RPC server process"
            if server_log_description is None
            else server_log_description
        )
        raise RPCError(
            f"The server could not send a reply to the call {call}. "
            f"The reason is recorded in {where}."
        )
    try:
        result = pickle.loads(body)
    except Exception as exc:
        raise RPCError(f"Could not read the body of the response to {call}: {exc}") from exc
    if isinstance(result, RemoteFailure):
        _raise_remote_error(result, call)
    return result


def _raise_remote_error(failure: RemoteFailure, call: RPCCall) -> NoReturn:
    """Raise a client-side exception for an RPC call that failed on the server.

    A usage error is re-raised as the class the server raised, without the server traceback,
    so that the user is confronted with their own mistake instead of StepUp's plumbing.
    Anything else indicates a bug in StepUp, for which the full traceback is what a bug
    report needs, so it is wrapped in an `RPCError` that embeds it.
    `STEPUP_DEBUG` selects the latter treatment for every exception.
    """
    if failure.usage and not is_debug():
        # `from None`: a chained `RPCError` would put StepUp's plumbing back in the traceback.
        raise failure.to_exception() from None
    raise RPCError(
        f"An exception was raised in the server during the call {call}: "
        f"\n\n{failure.traceback_text}"
    )


@attrs.define(frozen=True)
class RemoteCallProxy:
    """A proxy object to call remote procedures.

    A call is addressed by attribute lookup:
    the name of the remote procedure is the attribute, not an argument,
    and the positional and keyword arguments are forwarded to it verbatim:

    ```python
    client.call.some_procedure(1, two=2)
    ```

    The proxy exists to give that attribute lookup an empty namespace of its own.
    Were the procedures looked up on the client itself,
    every name the client defines would become a procedure name that cannot be called remotely.
    Here, only the dunder names are taken,
    and `__getattr__` runs for anything else,
    so any reasonable procedure name reaches the server.
    The one field is called `_client` for that reason,
    which frees `client` as a procedure name.

    The synchronous clients reserve the keyword argument `_rpc_timeout`,
    which bounds the wait for the response and is not forwarded.
    Its leading underscore is the only available protection against a remote procedure
    with a parameter of the same name.
    A keyword-only argument cannot be made collision-proof the way the procedure name is,
    which is passed positionally to `__call__` and shielded by the positional-only marker.
    """

    _client: "_BaseRPCClient" = attrs.field()
    """The RPC client that sends the call."""

    def __getattr__(self, name):
        """Return a function that calls the remote procedure `name`.

        Parameters
        ----------
        name
            The name of the remote procedure to call.

        Raises
        ------
        AttributeError
            For a dunder name, which is never a remote procedure.
            Without this, anything probing for a special method (`copy`, `pickle`, ...)
            would receive a function that sends an RPC call instead.
        """
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        return partial(self._client, name)


@attrs.define
class _BaseRPCClient:
    """Base class of every RPC client, synchronous or asynchronous, with or without a connection.

    All it promises is that a call can be sent,
    which is what makes a client without a connection interchangeable with one that has it.
    The bookkeeping that a client with an actual connection needs lives in `_SocketClientState`,
    and the contract for opening and closing that connection is in the module docstring.
    """

    @property
    def call(self) -> RemoteCallProxy:
        """Proxy that turns an attribute lookup into a call of the remote procedure by that name."""
        # A new proxy is built for every lookup, which is cheap and keeps this class stateless.
        # Caching one is not an option, and this constrains any change to the client classes:
        # the proxy would have to be stored here, because every client needs `call`,
        # which would give this class a non-empty `__slots__`.
        # A socket client inherits from `_SocketClientState` next to a client base,
        # and two slotted bases that both carry state cannot be combined.
        return RemoteCallProxy(self)


CLOSE_TIMEOUT = 5.0
"""The number of seconds a socket client may take to hand over the close message.

The close message is a courtesy to the server, see the module docstring,
so a client that cannot get rid of these few bytes drops the connection instead,
which tells the server the same thing.
Closing needs a limit that the calls do not provide, for a different reason on each side.
The asynchronous client may still have the requests of earlier calls in its write buffer,
which a server that has stopped reading never lets through.
The synchronous client would otherwise inherit the timeout of the last call,
which is unbounded after a call made with `NO_RPC_TIMEOUT`.
"""


@attrs.define
class _SocketClientState:
    """The bookkeeping shared by the RPC clients that talk to a server over a socket.

    This is mixed in next to `BaseAsyncRPCClient` or `BaseSyncRPCClient`,
    which contribute the calling half.
    It holds no connection and opens or closes nothing:
    the socket lives in the client that knows how to work it,
    which follows the contract in the module docstring.
    """

    socket_path: str = attrs.field()
    """The socket path of the server, see the section on endpoints.

    The client finds out which transport the server uses when it connects,
    and the client-side error messages refer to the server by this path.
    """

    server_log_description: str | None = attrs.field(default=None)
    """Where the server at the other end records the reason of a failure, e.g. a log file.

    This is a human-readable description, inserted verbatim into the error message of a call
    that the server could not reply to, so that the user is sent to the right place.
    `None` when nothing more specific than the server process itself can be said.
    """

    _counter: int = attrs.field(init=False, default=0)
    """The call id of the last message sent, needed to pair requests and responses.

    The counter also runs on for the close message,
    which therefore takes a call id of its own that no response will ever carry.
    """

    _closed: bool = attrs.field(init=False, default=False)
    """Whether `close()` was called, after which the client cannot be used anymore."""

    def _next_call_id(self) -> int:
        """Return the call id of the next message, unique within this connection."""
        self._counter += 1
        return self._counter

    def _check_usable(self):
        """Check that the client can still be used to talk to the server.

        Raises
        ------
        RPCClientUnusableError
            When the client is closed.
        """
        if self._closed:
            raise RPCClientUnusableError(f"The RPC client for socket {self.socket_path} is closed.")

    def _ignore_close_failure(self, exc: OSError):
        """Record that the close message could not be handed over, which the contract allows."""
        logger.debug(
            "Ignoring %r while closing the RPC client for socket %s", exc, self.socket_path
        )


#
# Asynchronous RPC clients
#


@attrs.define
class BaseAsyncRPCClient(_BaseRPCClient):
    """Base class for async RPC clients.

    A call waits for its response for as long as it takes.
    A caller that is not willing to wait indefinitely wraps the call in `asyncio.timeout`,
    which is why `__call__` takes no timeout argument, unlike in `BaseSyncRPCClient`.
    """

    async def __call__(self, name: str, /, *args, **kwargs) -> Any:
        """Call a function of the RPC server. This must be implemented in subclasses."""
        raise NotImplementedError

    async def close(self):
        """Close the client. A client without a connection has nothing to close."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, tb):
        await self.close()


@attrs.define(frozen=True)
class _PendingCall:
    """A call that was sent to the server and is waiting for its response."""

    call: RPCCall = attrs.field()
    """The call that was sent, rendered in the error message when no response can arrive."""

    future: asyncio.Future = attrs.field()
    """Receives the body of the response, or the error that says none can arrive."""


@attrs.define
class SocketAsyncRPCClient(_SocketClientState, BaseAsyncRPCClient):
    """Asynchronous RPC client for a server on a socket."""

    _pending: dict[int, _PendingCall] = attrs.field(init=False, factory=dict)
    """The calls waiting for a response, indexed by call id.

    A caller only adds entries.
    The receive loop is the one that resolves and removes them,
    so an entry left behind by a cancelled caller is discarded
    when its response arrives or when the connection ends.
    """

    _reader: asyncio.StreamReader | None = attrs.field(init=False, default=None)
    """The reader to receive responses from the server, `None` until the connection is opened."""

    _writer: asyncio.StreamWriter | None = attrs.field(init=False, default=None)
    """The writer to send requests to the server, `None` until the connection is opened."""

    _connect_task: asyncio.Task | None = attrs.field(init=False, default=None)
    """The task that opens the connection, created by the first call."""

    _recv_task: asyncio.Task | None = attrs.field(init=False, default=None)
    """The task running the receive loop, `None` until the connection is opened.

    It is kept in a field so it cannot be garbage-collected while the client is alive.
    """

    _stop_event: asyncio.Event = attrs.field(init=False, factory=asyncio.Event)
    """Set when the connection is being torn down, which ends the receive loop."""

    async def __call__(self, name: str, /, *args, **kwargs):
        """Call a function of the RPC server.

        Parameters
        ----------
        name, args, kwargs
            The remote procedure to call and its arguments, see `RemoteCallProxy`.

        Returns
        -------
        value
            Whatever the remote procedure returns.

        Raises
        ------
        ConnectionResetError
            When the connection to the server is lost before the response is received,
            or when it was already lost before the call was made.
            A call that is in flight when the receive loop ends raises this,
            whatever ended that loop.
        RPCClientUnusableError
            When the client is closed.
        RPCError
            When the call failed on the server,
            when the server could not send the reply,
            or when the receive loop ended on a message that is not an RPC message.
            The last case is raised by the calls made after the loop ended and by `close()`,
            because it is what explains that no response can arrive anymore.
        """
        await self._ensure_connected()
        call = RPCCall(name, args, kwargs)
        if self._recv_task.done():
            # No response can arrive anymore. When the receive loop failed,
            # its exception explains that better than a lost connection would.
            self._recv_task.result()
            raise ConnectionResetError(f"RPC connection lost before calling {call}")
        request = encode_body(call)
        call_id = self._next_call_id()
        future = asyncio.get_running_loop().create_future()
        # Nothing is awaited between the check above and this assignment,
        # so the receive loop cannot end in between and leave this entry behind,
        # which would make the caller wait for a future that nobody resolves anymore.
        self._pending[call_id] = _PendingCall(call, future)
        try:
            await send_stream_message(self._writer, call_id, request)
        except BaseException:
            # A request that was not sent gets no response, so this future is never awaited.
            # It must not be left behind for the receive loop to fail,
            # because an exception set on a future that nobody awaits is reported by asyncio
            # as `Future exception was never retrieved`.
            self._pending.pop(call_id, None)
            raise
        return _decode_response(
            await future, call, server_log_description=self.server_log_description
        )

    async def close(self):
        """Close the client, following the closing contract in the module docstring.

        This sends a close message to the server, closes the connection
        and waits for the receive loop to stop.
        The message is given `CLOSE_TIMEOUT` seconds to leave.

        Raises
        ------
        RPCError
            Whatever ended the receive loop, e.g. a response that is not an RPC message.
            The connection is already closed by then,
            so a client that raises here is still properly closed.
        """
        if self._closed:
            return
        self._closed = True
        if self._connect_task is None:
            return
        # Let a connection that is still being opened finish, so that it is not left dangling.
        # A connection that could not be opened at all has nothing to close,
        # and the call that tried to open it has already raised the reason.
        with contextlib.suppress(OSError, RPCError):
            await self._connect_task
        if self._writer is None:
            return
        handed_over = False
        try:
            async with asyncio.timeout(CLOSE_TIMEOUT):
                # An empty body is the close request,
                # see the wire format at the top of this module.
                await send_stream_message(self._writer, self._next_call_id(), None)
        except OSError as exc:
            # `asyncio.timeout` raises a `TimeoutError`, which is an `OSError`:
            # a close message that could not be handed over in time is treated like one
            # that could not be handed over at all.
            self._ignore_close_failure(exc)
        else:
            handed_over = True
        finally:
            self._stop_event.set()
            # The connection is closed before the receive loop is awaited,
            # because a loop that failed re-raises here and would otherwise leave it open.
            if handed_over:
                self._writer.close()
            else:
                # A graceful close flushes the write buffer first,
                # which a server that has stopped reading never accepts,
                # so the requests still waiting there would keep this close from returning.
                # This also covers a caller that cancels the close:
                # the wait below happens while the cancellation is already unwinding,
                # where a second cancellation cannot break it out anymore.
                self._writer.transport.abort()
            with suppress_peer_gone():
                await self._writer.wait_closed()
            await self._recv_task

    async def _ensure_connected(self):
        """Open the connection and start the receive loop, at most once.

        Concurrent callers share a single attempt,
        because the task is created without awaiting anything first:
        no other caller can slip in between the check and the assignment.

        Raises
        ------
        RPCClientUnusableError
            When the client is closed.
        OSError
            When the socket cannot be connected to.
        RPCError
            When the socket path holds a file that is not an endpoint file.
        """
        self._check_usable()
        if self._connect_task is None:
            self._connect_task = asyncio.create_task(
                self._open_connection(), name="client-rpc-connect"
            )
        await self._connect_task

    async def _open_connection(self):
        """Connect to the server and start the task that receives the responses."""
        endpoint = read_endpoint(self.socket_path)
        if endpoint is None:
            self._reader, self._writer = await asyncio.open_unix_connection(self.socket_path)
        else:
            self._reader, self._writer = await asyncio.open_connection(endpoint.host, endpoint.port)
            set_nodelay(self._writer.get_extra_info("socket"))
            # Not drained here: whatever is sent next drains it along.
            self._writer.write(endpoint.token)
        self._recv_task = asyncio.create_task(self._recv_loop(), name="client-rpc-recv-loop")

    async def _recv_loop(self):
        """Hand the responses received from the server to the callers waiting for them."""
        responses = iter_stream_messages(self._reader, self._stop_event)
        try:
            async with contextlib.aclosing(responses):
                async for call_id, response in responses:
                    pending = self._pending.pop(call_id, None)
                    if pending is None:
                        raise RPCError(f"Received a response for unknown call id {call_id}.")
                    if not pending.future.cancelled():
                        pending.future.set_result(response)
        finally:
            # The peer is gone, `close()` was called, or this loop is failing.
            # Either way, fail every pending call, so that it raises instead of
            # waiting for a response that can no longer arrive.
            while self._pending:
                _, pending = self._pending.popitem()
                if not pending.future.cancelled():
                    pending.future.set_exception(
                        ConnectionResetError(f"RPC connection lost while calling {pending.call}")
                    )


@attrs.define
class DummyAsyncRPCClient(BaseAsyncRPCClient):
    """Asynchronous client without a server, which prints the calls and returns `None`."""

    async def __call__(self, name: str, /, *args, **kwargs) -> None:
        """Print the call instead of sending it."""
        print(RPCCall(name, args, kwargs))


#
# Synchronous RPC clients and their timeouts
#


DEFAULT_SYNC_RPC_TIMEOUT = 600.0
"""The number of seconds a synchronous call waits for a response, unless told otherwise.

The environment variable `STEPUP_SYNC_RPC_TIMEOUT` overrides this default.
It is generous because a server may take arbitrarily long to answer a call,
which is not by itself a sign of a broken connection.
"""

NO_RPC_TIMEOUT = -1.0
"""The `_rpc_timeout` of a synchronous call that waits for as long as the server needs.

The director answers some calls only when the workflow is ready for it,
which is a property of the workflow and not of the connection,
so any timeout would only cut off a healthy wait.
Every value that is not strictly positive has this meaning,
but a call that waits on purpose says so with this constant.
"""


@cache
def _default_sync_rpc_timeout() -> float:
    """Return the number of seconds a synchronous call waits when it sets no timeout itself.

    The environment is read and parsed once per process,
    so every call that relies on the default waits equally long,
    even when the environment changes while the process runs.
    A value that cannot be parsed is not remembered,
    so it keeps raising instead of failing only on the first call that needs the default.

    Returns
    -------
    rpc_timeout
        The number of seconds taken from `STEPUP_SYNC_RPC_TIMEOUT`,
        or `DEFAULT_SYNC_RPC_TIMEOUT` when that variable is not defined.

    Raises
    ------
    ConfigError
        When `STEPUP_SYNC_RPC_TIMEOUT` does not hold a number.
    """
    text = os.environ.get("STEPUP_SYNC_RPC_TIMEOUT")
    if text is None:
        return DEFAULT_SYNC_RPC_TIMEOUT
    try:
        return float(text)
    except ValueError as exc:
        raise ConfigError(
            "The environment variable STEPUP_SYNC_RPC_TIMEOUT must hold a number "
            f"of seconds, got {text!r}."
        ) from exc


def _resolve_socket_timeout(rpc_timeout: float | None) -> float | None:
    """Turn the `_rpc_timeout` of a synchronous call into a timeout for the socket.

    Parameters
    ----------
    rpc_timeout
        The `_rpc_timeout` argument of the call, see `SocketSyncRPCClient.__call__`.

    Returns
    -------
    socket_timeout
        The number of seconds a socket operation may take,
        or `None` when it may take as long as the server needs.

    Raises
    ------
    ConfigError
        When `STEPUP_SYNC_RPC_TIMEOUT` does not hold a number,
        see `_default_sync_rpc_timeout`.
    """
    if rpc_timeout is None:
        rpc_timeout = _default_sync_rpc_timeout()
    return None if rpc_timeout <= 0 else rpc_timeout


@attrs.define
class BaseSyncRPCClient(_BaseRPCClient):
    """Base class for synchronous RPC clients.

    A blocking caller has no way to give up on a call from the outside,
    so `__call__` takes an `_rpc_timeout` argument to bound the wait from the inside,
    see `SocketSyncRPCClient.__call__`.
    """

    def __call__(self, name: str, /, *args, _rpc_timeout: float | None = None, **kwargs):
        """Call a function of the RPC server. This must be implemented in subclasses."""
        raise NotImplementedError

    def close(self):
        """Close the client. A client without a connection has nothing to close."""

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, tb):
        self.close()


@attrs.define
class SocketSyncRPCClient(_SocketClientState, BaseSyncRPCClient):
    """Synchronous RPC client for a server on a socket."""

    _socket: socket.socket | None = attrs.field(init=False, default=None)
    """The socket to communicate with the server."""

    _reader: SocketReader | None = attrs.field(init=False, default=None)
    """The buffered reader over the socket, created and dropped together with it."""

    _broken: bool = attrs.field(init=False, default=False)
    """Whether an exchange with the server was interrupted, e.g. by a timeout.

    Part of a message may then be left on the socket,
    which a later call would read back as its own response.
    There is no way to resynchronize, so the client cannot be used anymore.
    """

    def __call__(self, name: str, /, *args, _rpc_timeout: float | None = None, **kwargs) -> Any:
        """Call a function of the RPC server (always blocking).

        Parameters
        ----------
        name, args, kwargs
            The remote procedure to call and its arguments, see `RemoteCallProxy`.
        _rpc_timeout
            The timeout for the remote call in seconds,
            which also bounds the connect that the first call triggers.
            This keyword argument is not passed to the remote procedure.
            When None (the default), the timeout is taken from the environment variable
            `STEPUP_SYNC_RPC_TIMEOUT`, or from `DEFAULT_SYNC_RPC_TIMEOUT`
            when that variable is not defined.
            A negative or zero value, `NO_RPC_TIMEOUT` by convention,
            means that the client waits indefinitely for a response.

        Returns
        -------
        value
            Whatever the remote procedure returns.

        Raises
        ------
        RPCError
            When the call failed on the server,
            or when the server could not send the reply.
        RPCClientUnusableError
            When the client can no longer be used, see `_check_usable`.
        TimeoutError
            When the server does not answer within the timeout.
            The client cannot be used anymore after this,
            because the response may still be on its way.
        ConfigError
            When `STEPUP_SYNC_RPC_TIMEOUT` does not hold a number,
            see `_resolve_socket_timeout`.
        """
        sock = self._ensure_connected(_resolve_socket_timeout(_rpc_timeout))
        call = RPCCall(name, args, kwargs)
        request = encode_body(call)
        call_id = self._next_call_id()
        # Anything that interrupts the exchange, up to and including a `KeyboardInterrupt`,
        # leaves the connection in an unknown state, so the client is broken until proven
        # otherwise: the flag is only cleared once the full response has been read.
        self._broken = True
        send_socket_message(sock, call_id, request)
        response = self._recv_response(call_id)
        self._broken = False
        return _decode_response(response, call, server_log_description=self.server_log_description)

    def close(self):
        """Close the client, following the closing contract in the module docstring.

        This sends a close message to the server, after which the server should stop eventually,
        and closes the socket.
        A client whose connection is out of sync sends no message either,
        because the server has no way to make sense of it.
        The message is given `CLOSE_TIMEOUT` seconds to leave.
        """
        if self._closed:
            return
        self._closed = True
        if self._socket is None:
            return
        try:
            if not self._broken:
                self._socket.settimeout(CLOSE_TIMEOUT)
                # An empty body is the close request,
                # see the wire format at the top of this module.
                send_socket_message(self._socket, self._next_call_id(), None)
        except OSError as exc:
            self._ignore_close_failure(exc)
        finally:
            self._socket.close()
            self._socket = None
            self._reader = None

    def _check_usable(self):
        """Also refuse a client whose connection is out of sync.

        Raises
        ------
        RPCClientUnusableError
            When the client is closed,
            or when an earlier call left the connection out of sync.
        """
        super()._check_usable()
        if self._broken:
            raise RPCClientUnusableError(
                f"The RPC client for socket {self.socket_path} lost track of the conversation "
                "with the server and cannot be used anymore."
            )

    def _ensure_connected(self, socket_timeout: float | None) -> socket.socket:
        """Return the socket ready for one exchange, connecting on the first call.

        The timeout is installed on every call, not only when the socket is created,
        because it is the `_rpc_timeout` of the call that is about to be made.

        This runs once per exchange, before `_broken` is set,
        which is what makes the check of `_check_usable` compatible with that flag
        being set for the duration of the exchange.

        Parameters
        ----------
        socket_timeout
            The number of seconds each socket operation may take,
            or `None` to let it take as long as needed.
            It is applied before connecting,
            so that it also bounds the connect that the first call triggers.

        Returns
        -------
        sock
            The connected socket, with the timeout of this exchange installed.

        Raises
        ------
        RPCClientUnusableError
            When the client can no longer be used, see `_check_usable`.
        OSError
            When the socket cannot be connected to.
            A later call tries to connect again.
        RPCError
            When the socket path holds a file that is not an endpoint file.
        TimeoutError
            When connecting takes longer than `socket_timeout`.
        """
        self._check_usable()
        if self._socket is None:
            self._socket = self._connect(socket_timeout)
            self._reader = SocketReader(self._socket, self.socket_path)
        else:
            self._socket.settimeout(socket_timeout)
        return self._socket

    def _connect(self, socket_timeout: float | None) -> socket.socket:
        """Open a connection to the server, over the transport that the socket path reveals.

        Parameters
        ----------
        socket_timeout
            See `_ensure_connected`.

        Returns
        -------
        sock
            The connected socket, with the timeout installed.
            On the TCP transport, the token has been sent already.
        """
        endpoint = read_endpoint(self.socket_path)
        if endpoint is None:
            sock = socket.socket(socket.AF_UNIX)
            try:
                sock.settimeout(socket_timeout)
                sock.connect(self.socket_path)
            except BaseException:
                sock.close()
                raise
            return sock
        sock = socket.create_connection((endpoint.host, endpoint.port), timeout=socket_timeout)
        try:
            set_nodelay(sock)
            sock.sendall(endpoint.token)
        except BaseException:
            sock.close()
            raise
        return sock

    def _recv_response(self, expected_call_id: int) -> bytes | None:
        """Receive a single RPC response.

        Parameters
        ----------
        expected_call_id
            The call id of the request that this response must answer.

        Returns
        -------
        body
            The body of the response,
            or `None` when the server could not send the reply.

        Raises
        ------
        RPCError
            When the response answers another call than the one that was just made,
            which means that the conversation with the server is out of step.
        ConnectionResetError
            When the server is gone, see `recv_socket_message`.
        """
        call_id, body = recv_socket_message(self._reader)
        if call_id != expected_call_id:
            raise RPCError(
                f"Received a response for call id {call_id} while waiting for the response "
                f"to call id {expected_call_id}."
            )
        return body


@attrs.define
class DummySyncRPCClient(BaseSyncRPCClient):
    """Synchronous client without a server, which returns `None` for every call.

    When `STEPUP_DEBUG` is set, it prints each call on standard error,
    so that the standard output of the calling script stays clean.
    """

    def __call__(self, name: str, /, *args, _rpc_timeout: float | None = None, **kwargs) -> None:
        """Ignore the call, or print it when `STEPUP_DEBUG` is set.

        The timeout is accepted and ignored.
        """
        if is_debug():
            print(RPCCall(name, args, kwargs), file=sys.stderr)
