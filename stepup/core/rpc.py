# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Lightweight and versatile RPC implementation using asyncio stream reader and writer.

This module also includes a synchronous RPC client to support simple client APIs.

A server is given a handler: any object whose methods, decorated with `@allow_rpc`,
a client may call remotely.
Only decorated methods can be called, which bounds what a client may ask for by name.

The bodies of requests and responses are unpickled,
so the access permissions of the Unix domain socket are the trust boundary:
whoever can connect to it can execute code in the process at the other end,
whether a method allows it or not.

The clients that talk to a server over a Unix domain socket,
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
import importlib
import inspect
import logging
import os
import pickle
import socket
import traceback
from collections.abc import AsyncIterator, Callable
from functools import cache, partial
from typing import Any, NoReturn, TypeVar

import attrs

from .asyncio import _wait_closed_compat, iter_until_stopped
from .exceptions import ConfigError, RPCClientUnusableError, RPCError, UsageError
from .utils import is_debug

logger = logging.getLogger(__name__)

__all__ = (
    "NO_RPC_TIMEOUT",
    "BaseAsyncRPCClient",
    "BaseSyncRPCClient",
    "DummyAsyncRPCClient",
    "DummySyncRPCClient",
    "SocketAsyncRPCClient",
    "SocketRPCServer",
    "SocketSyncRPCClient",
    "allow_rpc",
    "is_rpc_allowed",
)

FuncType = TypeVar("FuncType", bound=Callable)


#
# The wire format
#
# A message is a header of two big-endian unsigned integers, followed by a body of the
# announced size:
#
#     | call id (8 bytes) | body size (8 bytes) | body (size bytes) |
#
# The call id pairs a request with its response:
# a response carries the call id of the request it answers.
#
# A body size of zero carries no body at all and acts as a sentinel.
# From client to server it is the close request:
# the client is done and expects no reply.
# From server to client it says that no reply is coming for that call id,
# for example because the returned value could not be pickled.
#
# A header that announces a body larger than `MAX_BODY_SIZE` is not the header of an RPC message.
# Reading one is reported as an error and never as a peer that is gone,
# because the connection cannot be resynchronized.
#


FIELD_SIZE = 8
"""The size in bytes of each of the two integer fields in the header of an RPC message."""

HEADER_SIZE = 2 * FIELD_SIZE
"""The size in bytes of the header of an RPC message."""

MAX_BODY_SIZE = 2**32
"""The largest body size accepted from a peer.

A pickled RPC payload is orders of magnitude smaller,
so this limit only ever fires on a header that is not one,
which would otherwise make the reader wait for bytes that are never coming.
"""


def _encode_message(call_id: int, body: bytes | None) -> bytes:
    """Build the bytes of a single RPC message.

    Parameters
    ----------
    call_id
        The call id of the message, see the wire format above.
    body
        The body of the message, or `None` to send an empty body.
        An empty body is a sentinel, see the wire format above.
        An empty `bytes` object is framed as an empty body too,
        so it is read back as the sentinel and not as a message of its own.

    Returns
    -------
    data
        The header and the body, ready to be sent in one go.
    """
    size = 0 if body is None else len(body)
    header = call_id.to_bytes(FIELD_SIZE, "big") + size.to_bytes(FIELD_SIZE, "big")
    return header if body is None else header + body


def _decode_header(header: bytes) -> tuple[int, int]:
    """Split the header of an RPC message into its two fields.

    There is no `_decode_message` to pair with `_encode_message`,
    because the header announces the size of the body that follows,
    so a reader cannot ask for the whole message in one go.

    Parameters
    ----------
    header
        The first `HEADER_SIZE` bytes of the message.

    Returns
    -------
    call_id
        The call id of the message.
    size
        The size in bytes of the body that follows, zero for the sentinel.

    Raises
    ------
    RPCError
        When the announced size exceeds `MAX_BODY_SIZE`,
        meaning that these bytes are not the header of an RPC message.
    """
    call_id = int.from_bytes(header[:FIELD_SIZE], "big")
    size = int.from_bytes(header[FIELD_SIZE:], "big")
    if size > MAX_BODY_SIZE:
        raise RPCError(f"RPC body size {size} exceeds the maximum of {MAX_BODY_SIZE} bytes.")
    return call_id, size


#
# Transport: asyncio streams
#


async def _recv_stream_message(reader: asyncio.StreamReader) -> tuple[int, bytes | None] | None:
    """Read a single RPC message from an `asyncio` stream.

    Parameters
    ----------
    reader
        The `asyncio.StreamReader` to read the next message from.

    Returns
    -------
    message
        The call id and the body of the message, see the wire format above,
        or `None` when the peer is gone, in which case the RPC loops should be stopped.

    Raises
    ------
    RPCError
        When the header is not the header of an RPC message, see `_decode_header`.
    """
    try:
        call_id, size = _decode_header(await reader.readexactly(HEADER_SIZE))
        body = None if size == 0 else await reader.readexactly(size)
    except (asyncio.IncompleteReadError, ConnectionError):
        # IncompleteReadError is a graceful EOF (peer closed cleanly). A reset while a read
        # is pending instead surfaces as a raw ConnectionError (e.g. ConnectionResetError).
        # Both mean the same thing here: the peer is gone, so the RPC loop should stop.
        return None
    return call_id, body


async def _send_stream_message(writer: asyncio.StreamWriter, call_id: int, body: bytes | None):
    """Send a single RPC message on an `asyncio` stream.

    Parameters
    ----------
    writer
        The `asyncio.StreamWriter` to write the message to.
    call_id, body
        See `_encode_message`.
    """
    writer.write(_encode_message(call_id, body))
    await writer.drain()


async def _iter_stream_messages(
    reader: asyncio.StreamReader, stop_event: asyncio.Event
) -> AsyncIterator[tuple[int, bytes | None]]:
    """Iterate over the RPC messages sent by the peer, until it is gone or `stop_event` is set.

    Parameters
    ----------
    reader
        The `asyncio.StreamReader` to read the messages from.
    stop_event
        The iteration ends when this event is set.
        It is set here when the peer is gone,
        so that the other loops serving the same connection stop as well.

    Yields
    ------
    call_id, body
        A message as read by `_recv_stream_message`,
        never the `None` that says that the peer is gone.

    Notes
    -----
    A consumer that may stop iterating early wraps this in `contextlib.aclosing`,
    for the reason given in `iter_until_stopped`.
    """
    messages = iter_until_stopped(partial(_recv_stream_message, reader), stop_event)
    async with contextlib.aclosing(messages):
        async for message in messages:
            if message is None:
                stop_event.set()
                return
            yield message


#
# Transport: blocking sockets
#
# The same messages as above, for a client that has no event loop to await them in.
#


@attrs.define
class _SocketReader:
    """Buffered reader over a blocking socket, the counterpart of `asyncio.StreamReader`."""

    sock: socket.socket = attrs.field()
    """The socket to read from."""

    socket_path: str = attrs.field()
    """The path to the Unix domain socket, which the error messages refer to."""

    _buffer: bytes = attrs.field(init=False, default=b"")
    """The bytes received from the socket that are not yet consumed."""

    def readexactly(self, size: int) -> bytes:
        """Keep reading from the socket until at least `size` bytes have been received.

        Parameters
        ----------
        size
            The length of the byte sequence to receive.

        Returns
        -------
        data
            The bytes read from the socket of the requested size.
            Any additional data received from the socket is kept for the following call.

        Raises
        ------
        ConnectionResetError
            When the socket returns zero bytes, meaning that the peer is gone.
        """
        while len(self._buffer) < size:
            fragment = self.sock.recv(4096)
            if len(fragment) == 0:
                raise ConnectionResetError(
                    f"The RPC server on socket {self.socket_path} closed the connection "
                    f"while {size - len(self._buffer)} more bytes were expected."
                )
            self._buffer += fragment
        result = self._buffer[:size]
        self._buffer = self._buffer[size:]
        return result


def _recv_socket_message(reader: _SocketReader) -> tuple[int, bytes | None]:
    """Read a single RPC message from a blocking socket.

    Parameters
    ----------
    reader
        The `_SocketReader` to read the next message from.

    Returns
    -------
    call_id, body
        The call id and the body of the message, see the wire format above.

    Raises
    ------
    ConnectionResetError
        When the peer is gone, see `_SocketReader.readexactly`.
        A blocking caller is waiting for one specific message,
        so a peer that is gone is an error here,
        while `_recv_stream_message` reports it as the end of the messages.
    RPCError
        When the header is not the header of an RPC message, see `_decode_header`.
    """
    call_id, size = _decode_header(reader.readexactly(HEADER_SIZE))
    return call_id, None if size == 0 else reader.readexactly(size)


def _send_socket_message(sock: socket.socket, call_id: int, body: bytes | None):
    """Send a single RPC message on a blocking socket.

    Parameters
    ----------
    sock
        The socket to write the message to.
    call_id, body
        See `_encode_message`.
    """
    sock.sendall(_encode_message(call_id, body))


#
# The payloads on the wire
#
# A body is a pickled object: an `RPCCall` from client to server,
# a result or a `RemoteFailure` from server to client.
# Both directions encode the same way, see `_encode_body`,
# while each direction reads the bodies it receives in its own section,
# see `_decode_request` and `_decode_response`.
#


@attrs.define(frozen=True)
class RPCCall:
    """A call of a remote procedure, as it travels from the client to the server."""

    name: str = attrs.field()
    """The name of the remote procedure to call."""

    args: tuple = attrs.field(converter=tuple, default=())
    """The positional arguments to call it with."""

    kwargs: dict = attrs.field(factory=dict)
    """The keyword arguments to call it with."""

    def __str__(self) -> str:
        """Format the call the way it would be written in Python, used in error messages."""
        all_args = [repr(arg) for arg in self.args]
        all_args.extend(f"{key}={value!r}" for key, value in self.kwargs.items())
        return f"{self.name}({', '.join(all_args)})"


@attrs.define(frozen=True)
class RemoteFailure:
    """The server-side exception of a failed RPC call, as sent back to the client.

    Only strings and a bool, so that the reply is always picklable,
    no matter how exotic the original exception is.
    """

    module: str = attrs.field()
    """The `__module__` of the exception class, e.g. `stepup.core.exceptions`."""

    qualname: str = attrs.field()
    """The `__qualname__` of the exception class, e.g. `CyclicError`."""

    message: str = attrs.field()
    """The result of `str(exc)`."""

    traceback_text: str = attrs.field()
    """The formatted server-side traceback."""

    usage: bool = attrs.field()
    """Whether the exception is a `UsageError`, i.e. a mistake the user can fix.

    This is decided on the server, where the exception class is guaranteed importable,
    rather than reconstructed on the client from the type name.
    """

    @classmethod
    def from_exception(cls, exc: BaseException) -> "RemoteFailure":
        """Summarize an exception raised while handling an RPC call."""
        return cls(
            type(exc).__module__,
            type(exc).__qualname__,
            str(exc),
            "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
            isinstance(exc, UsageError),
        )

    def to_exception(self) -> BaseException:
        """Recreate the server-side `UsageError` in the client process.

        Every step is guarded and falls back to an `RPCError` with the original message,
        so that a payload which cannot be reconstructed faithfully
        never makes the client raise something arbitrary.

        Returns
        -------
        exc
            An instance of the original exception class,
            or an `RPCError` when the class could not be reconstructed.
            The server-side traceback is not carried over.
        """
        try:
            cls = getattr(importlib.import_module(self.module), self.qualname)
        except (ImportError, AttributeError):
            # The class is not importable in the client process.
            return RPCError(self.message)
        if not (isinstance(cls, type) and issubclass(cls, UsageError)):
            # Checking `UsageError` (and not `Exception`) keeps the reconstruction inside the set
            # of classes this design vouches for: a plain-message constructor and a meaning the
            # user can act on.
            return RPCError(self.message)
        try:
            return cls(self.message)
        except TypeError:
            # A subclass with a richer constructor signature.
            return RPCError(self.message)


def _encode_body(payload: Any) -> bytes:
    """Build the body of an RPC message, in either direction.

    Parameters
    ----------
    payload
        The `RPCCall` of a request, or the result or `RemoteFailure` of a response.

    Returns
    -------
    body
        The body to send, whose size the header announces.
    """
    return pickle.dumps(payload, protocol=pickle.HIGHEST_PROTOCOL)


#
# RPC server, always async
#
# The handler, defined in the module docstring, is passed on unchanged
# from the entry point to the connection that eventually calls it.
#


def allow_rpc(func: FuncType) -> FuncType:
    """Decorator to allow a function to be called remotely.

    A client can only call the methods of a handler that carry this decorator,
    which `_call_procedure` enforces.
    """
    func._allow_rpc = True
    return func


def is_rpc_allowed(func: Callable) -> bool:
    """Check whether a function or method was decorated with `allow_rpc`."""
    return getattr(func, "_allow_rpc", False)


def _decode_request(body: bytes) -> RPCCall:
    """Take the body of an RPC request apart.

    Parameters
    ----------
    body
        The content of the request, as received from the client.

    Returns
    -------
    call
        The call that the client wants the server to perform.

    Raises
    ------
    RPCError
        When the body is not the request of an RPC client,
        e.g. because the peer speaks another protocol.
    """
    try:
        call = pickle.loads(body)
    except Exception as exc:
        raise RPCError(f"Could not read the body of an RPC request: {exc}") from exc
    if not isinstance(call, RPCCall):
        raise RPCError(f"The body of an RPC request holds a {type(call).__name__}, not a call.")
    return call


async def _call_procedure(handler: object, call: RPCCall) -> Any:
    """Call a remote procedure of the handler, after checking that it may be called at all.

    Parameters
    ----------
    handler
        The object whose methods are called remotely.
    call
        The call to perform, as received from the client.

    Returns
    -------
    result
        Whatever the remote procedure returns, awaited when it returns an awaitable.

    Raises
    ------
    RPCError
        When the handler has no such procedure, when it was not decorated with `@allow_rpc`,
        or when the arguments do not fit its signature.
    """
    try:
        procedure = getattr(handler, call.name)
    except AttributeError as exc:
        raise RPCError(f"Unknown remote procedure {call.name}") from exc
    if not is_rpc_allowed(procedure):
        raise RPCError(f"Remote procedure {call.name} exists but is not allowed")
    try:
        # Only to reject arguments that the procedure cannot be called with, ignoring type hints.
        inspect.signature(procedure).bind(*call.args, **call.kwargs)
    except TypeError as exc:
        raise RPCError(f"Invalid arguments: {call}") from exc
    result = procedure(*call.args, **call.kwargs)
    return await result if inspect.isawaitable(result) else result


async def _call_and_capture_failure(handler: object, call: RPCCall) -> Any:
    """Perform an RPC call for a client and turn a failure into a reply of its own.

    This coroutine never raises, so a call that was started always ends in a reply.
    A task wrapping it can still end cancelled,
    namely when the cancellation arrives before the coroutine starts running
    and there is no call yet whose failure could be captured.

    Parameters
    ----------
    handler, call
        See `_call_procedure`.

    Returns
    -------
    result
        The result of the call, or the `RemoteFailure` describing why it failed.
        A remote procedure must not return a `RemoteFailure` of its own,
        because that is how the client recognizes a call that failed.
    """
    try:
        return await _call_procedure(handler, call)
    except BaseException as exc:  # noqa: BLE001
        # Every exception becomes a reply, including the `CancelledError` of a handler that was
        # cancelled: a client is waiting for an answer to this call and gets one either way.
        failure = RemoteFailure.from_exception(exc)
        # Keep a server-side record of the traceback, because the client may hide it.
        # `WARNING` and not `INFO`: the director's default log level is `WARNING`
        # (see `director.py`), so an `INFO` record would be dropped in exactly the case
        # this record exists for, i.e. a build without `STEPUP_DEBUG`.
        # `WARNING` and not `ERROR`: the last pattern in `DIRECTOR_LOG_CHECKS` (`utils.py`)
        # anchors on the level field, so an `ERROR` record here would turn every reported
        # usage error into a build finding.
        logger.warning("Exception in RPC call %s:\n%s", call, failure.traceback_text)
        return failure


@attrs.define(eq=False)
class RPCServerConnection:
    """Serve the RPC calls of a single client, until the peer is gone or `stop` is called.

    The reader and writer must be connected to an RPC client implemented in this module,
    which normally ends the connection by closing its client.
    A server that is shutting down uses `stop` instead,
    so that it does not have to wait for a peer that keeps its side open.

    Instances use identity-based equality and hashing,
    since two connections are distinct even when they serve the same handler.
    """

    handler: object = attrs.field()
    """The RPC handler."""

    reader: asyncio.StreamReader = attrs.field()
    """The RPC calls are received from this reader."""

    writer: asyncio.StreamWriter = attrs.field()
    """The RPC results or exceptions are written to this writer."""

    _stop_event: asyncio.Event = attrs.field(init=False, factory=asyncio.Event)
    """Set when the connection is being torn down, which ends both loops."""

    _completed: asyncio.Queue[tuple[int, asyncio.Task]] = attrs.field(
        init=False, factory=asyncio.Queue
    )
    """The completed calls whose response is not sent yet, as `(call_id, task)` pairs."""

    _tasks: set[asyncio.Task] = attrs.field(init=False, factory=set)
    """The calls in flight, kept alive here so they cannot be garbage-collected mid-call."""

    def stop(self):
        """End both loops, without waiting for the peer to close the connection.

        The calls that are in flight are still completed,
        so this waits for the handlers of a connection with a call in flight,
        while an idle connection ends at once.
        A reply that the send loop has not written yet may be dropped,
        which is acceptable on every path that ends the loops:
        the server is going away, the peer is gone,
        or the peer announced that it wants no more replies.
        """
        self._stop_event.set()

    async def serve(self):
        """Serve the connection until the peer is gone, then close this side of it.

        The calls that are still in flight when the connection ends are completed first,
        so that a request which arrived in full is applied in full.
        A connection that is torn down by a failing loop or by a cancellation
        takes its handlers with it instead.

        Raises
        ------
        ExceptionGroup
            When one of the two loops fails.
            The other loop is cancelled first,
            so that neither keeps serving a connection that is being torn down.
        """
        try:
            async with asyncio.TaskGroup() as task_group:
                task_group.create_task(self._recv_loop(), name="server-rpc-recv-loop")
                task_group.create_task(self._send_loop(), name="server-rpc-send-loop")
        finally:
            # A peer that is already gone cannot be told that the connection is closing,
            # which is no reason to leave it open on this side.
            with contextlib.suppress(ConnectionError):
                await self.writer.drain()
            self.writer.close()
            with contextlib.suppress(ConnectionError):
                await self.writer.wait_closed()

    async def _recv_loop(self):
        """Receive requests from the client and create a task for each of them.

        The calls that are in flight when the loop ends are finished before it returns,
        so that no handler outlives the connection it was created for.
        """
        try:
            requests = _iter_stream_messages(self.reader, self._stop_event)
            async with contextlib.aclosing(requests):
                async for call_id, request in requests:
                    if request is None:
                        # The close request of the client,
                        # see the wire format at the top of this module.
                        self._stop_event.set()
                        break
                    # A body that is not an `RPCCall` means the peer is not speaking this
                    # protocol, so the rest of the connection cannot be trusted either.
                    # The `RPCError` therefore ends the connection instead of becoming a
                    # `RemoteFailure` reply, unlike a call whose procedure raises.
                    call = _decode_request(request)
                    task = asyncio.create_task(
                        _call_and_capture_failure(self.handler, call),
                        name=f"RPC:{call.name}-{call_id}",
                    )
                    self._tasks.add(task)
                    task.add_done_callback(partial(self._queue_reply, call_id))
        except BaseException:
            # This loop is failing or is being cancelled, so the connection is not merely
            # ending: the handlers are cancelled along with it, and the `finally` below
            # then only has to wait for them to notice.
            # Iterate over a copy: `_queue_reply` removes a task from the set when it completes.
            for task in list(self._tasks):
                task.cancel()
            raise
        finally:
            # The connection is ending, but a request that was received in full describes a
            # complete mutation, so its handler is given the chance to apply all of it.
            # A handler that is cancelled between two transactions applies only part of one.
            # `return_exceptions`: a handler that ends on an exception must not replace the
            # exception that is unwinding this loop, and it is not this loop's to report anyway,
            # since `_call_and_capture_failure` turns a failed call into a reply of its own.
            await asyncio.gather(*self._tasks, return_exceptions=True)

    def _queue_reply(self, call_id: int, task: asyncio.Task):
        """Hand a completed task to the send loop."""
        self._tasks.discard(task)
        self._completed.put_nowait((call_id, task))

    async def _send_loop(self):
        """Send the replies of completed tasks back to the client."""
        completed_tasks = iter_until_stopped(self._completed.get, self._stop_event)
        async with contextlib.aclosing(completed_tasks):
            async for call_id, task in completed_tasks:
                if task.cancelled():
                    # The receive loop cancels the calls in flight when it tears the connection
                    # down, and the done callback queues them here like any other completed task.
                    # There is nobody left to answer, so the reply is dropped instead of
                    # letting the `CancelledError` of `await task` end this loop silently.
                    continue
                try:
                    response = _encode_body(await task)
                    await _send_stream_message(self.writer, call_id, response)
                except ConnectionError:
                    # The peer is already gone: no point notifying it or serving this connection
                    # any further.
                    self._stop_event.set()
                    return
                except Exception:
                    # Some other failure, e.g. an unpicklable result.
                    # Try to tell the client that no reply is coming,
                    # with the empty body of the wire format at the top of this module.
                    # Don't let a doomed notification attempt mask the original exception.
                    with contextlib.suppress(ConnectionError):
                        await _send_stream_message(self.writer, call_id, None)
                    raise


@attrs.define(eq=False)
class SocketRPCServer:
    """Accept connections on a Unix domain socket and serve the RPC calls they carry.

    Instances use identity-based equality, for the same reason as `RPCServerConnection`.
    """

    handler: object = attrs.field()
    """The RPC handler."""

    socket_path: str = attrs.field()
    """The path to the Unix domain socket."""

    _connections: set[RPCServerConnection] = attrs.field(init=False, factory=set)
    """The connections currently being served."""

    async def serve(self, stop_event: asyncio.Event):
        """Accept and serve connections until `stop_event` is set.

        Parameters
        ----------
        stop_event
            The server keeps accepting connections until this event is set,
            and then stops the connections that are still open.
        """
        server = await asyncio.start_unix_server(self._serve_connection, self.socket_path)
        await stop_event.wait()
        # Closing the server only stops it from accepting,
        # so a connection whose peer keeps its side open keeps being served.
        # Since `wait_closed` below waits for every connection being served,
        # such a peer would decide when this method returns.
        # Iterate over a copy: `_serve_connection` removes a connection when it stops serving.
        for connection in list(self._connections):
            connection.stop()
        server.close()
        await server.wait_closed()
        await _wait_closed_compat(server)

    async def _serve_connection(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        """Serve a single accepted connection, as `asyncio.start_unix_server` expects."""
        connection = RPCServerConnection(self.handler, reader, writer)
        self._connections.add(connection)
        try:
            await connection.serve()
        finally:
            self._connections.discard(connection)


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
    """The bookkeeping shared by the RPC clients that talk to a server over a Unix domain socket.

    This is mixed in next to `BaseAsyncRPCClient` or `BaseSyncRPCClient`,
    which contribute the calling half.
    It holds no connection and opens or closes nothing:
    the socket lives in the client that knows how to work it,
    which follows the contract in the module docstring.
    """

    socket_path: str = attrs.field()
    """The path to the Unix domain socket, which the client-side error messages refer to."""

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
    """Asynchronous RPC client for a server on a Unix domain socket."""

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
        request = _encode_body(call)
        call_id = self._next_call_id()
        future = asyncio.get_running_loop().create_future()
        # Nothing is awaited between the check above and this assignment,
        # so the receive loop cannot end in between and leave this entry behind,
        # which would make the caller wait for a future that nobody resolves anymore.
        self._pending[call_id] = _PendingCall(call, future)
        try:
            await _send_stream_message(self._writer, call_id, request)
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
        # A connection that could not be opened at all has nothing to close.
        with contextlib.suppress(OSError):
            await self._connect_task
        if self._writer is None:
            return
        handed_over = False
        try:
            async with asyncio.timeout(CLOSE_TIMEOUT):
                # An empty body is the close request,
                # see the wire format at the top of this module.
                await _send_stream_message(self._writer, self._next_call_id(), None)
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
            with contextlib.suppress(ConnectionError):
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
        """
        self._check_usable()
        if self._connect_task is None:
            self._connect_task = asyncio.create_task(
                self._open_connection(), name="client-rpc-connect"
            )
        await self._connect_task

    async def _open_connection(self):
        """Connect to the socket and start the task that receives the responses."""
        self._reader, self._writer = await asyncio.open_unix_connection(self.socket_path)
        self._recv_task = asyncio.create_task(self._recv_loop(), name="client-rpc-recv-loop")

    async def _recv_loop(self):
        """Hand the responses received from the server to the callers waiting for them."""
        responses = _iter_stream_messages(self._reader, self._stop_event)
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
    """Synchronous RPC client for a server on a Unix domain socket."""

    _socket: socket.socket | None = attrs.field(init=False, default=None)
    """The socket to communicate with the server."""

    _reader: _SocketReader | None = attrs.field(init=False, default=None)
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
        request = _encode_body(call)
        call_id = self._next_call_id()
        # Anything that interrupts the exchange, up to and including a `KeyboardInterrupt`,
        # leaves the connection in an unknown state, so the client is broken until proven
        # otherwise: the flag is only cleared once the full response has been read.
        self._broken = True
        _send_socket_message(sock, call_id, request)
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
                _send_socket_message(self._socket, self._next_call_id(), None)
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
        TimeoutError
            When connecting takes longer than `socket_timeout`.
        """
        self._check_usable()
        if self._socket is None:
            self._socket = socket.socket(socket.AF_UNIX)
            self._socket.settimeout(socket_timeout)
            self._socket.connect(self.socket_path)
            self._reader = _SocketReader(self._socket, self.socket_path)
        else:
            self._socket.settimeout(socket_timeout)
        return self._socket

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
            When the server is gone, see `_recv_socket_message`.
        """
        call_id, body = _recv_socket_message(self._reader)
        if call_id != expected_call_id:
            raise RPCError(
                f"Received a response for call id {call_id} while waiting for the response "
                f"to call id {expected_call_id}."
            )
        return body


@attrs.define
class DummySyncRPCClient(BaseSyncRPCClient):
    """Synchronous client without a server, which prints the calls and returns `None`."""

    def __call__(self, name: str, /, *args, _rpc_timeout: float | None = None, **kwargs) -> None:
        """Print the call instead of sending it. The timeout is accepted and ignored."""
        print(RPCCall(name, args, kwargs))
