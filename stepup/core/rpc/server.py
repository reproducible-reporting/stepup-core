# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Serve the RPC calls of clients on a Unix domain socket or a TCP socket."""

import asyncio
import contextlib
import hmac
import inspect
import logging
import os
import pickle
import secrets
import socket
from collections.abc import Awaitable, Callable
from functools import partial
from typing import Any, Self, TypeVar

import attrs

from ..exceptions import ConsistencyError, RPCError
from ..path import Path
from ..platform.asyncio import is_aborted_accept, iter_until_stopped
from .protocol import (
    RemoteFailure,
    RPCCall,
    encode_body,
    is_peer_gone,
    iter_stream_messages,
    send_stream_message,
    suppress_peer_gone,
)
from .transport import RPC_TRANSPORTS, TCPEndpoint, set_nodelay

__all__ = (
    "SocketRPCServer",
    "allow_rpc",
    "is_rpc_allowed",
)

logger = logging.getLogger(__name__)


FuncType = TypeVar("FuncType", bound=Callable)

TCP_HOST = "127.0.0.1"
"""The address at which a server on the TCP transport listens, which only this machine can reach."""

TOKEN_SIZE = 32
"""The number of random bytes in the token that a client on the TCP transport sends first."""

AUTH_TIMEOUT = 10.0
"""The number of seconds a server on the TCP transport waits for the token of a new connection.

A client of this module sends its token right after connecting,
so only a peer that does not speak this protocol makes the server wait this long.
"""

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
        # (see `director/__main__.py`), so an `INFO` record would be dropped in exactly the case
        # this record exists for, i.e. a build without `STEPUP_DEBUG`.
        # `WARNING` and not `ERROR`:
        # the last pattern in `DIRECTOR_LOG_CHECKS` (`cli/director_log.py`)
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

    token: bytes | None = attrs.field(default=None, repr=False)
    """The token that the client must send before its first request.

    `None` when the transport needs no token, see the module docstring.
    """

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
            with suppress_peer_gone():
                await self.writer.drain()
            self.writer.close()
            with suppress_peer_gone():
                await self.writer.wait_closed()

    async def _recv_loop(self):
        """Receive requests from the client and create a task for each of them.

        The calls that are in flight when the loop ends are finished before it returns,
        so that no handler outlives the connection it was created for.
        """
        try:
            if not await self._authenticate():
                self._stop_event.set()
                return
            requests = iter_stream_messages(self.reader, self._stop_event)
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

    async def _authenticate(self) -> bool:
        """Read the token that the client must send first, if any, and check it.

        Nothing else is read from the connection before the token is accepted,
        so a peer without the right token cannot get anything unpickled.

        Returns
        -------
        accepted
            Whether the connection may carry requests.
            Without a token to check, this is always the case.
            Otherwise, the right token must arrive within `AUTH_TIMEOUT` seconds,
            before the peer is gone and before the connection is stopped.
        """
        if self.token is None:
            return True
        tokens = iter_until_stopped(partial(self.reader.readexactly, TOKEN_SIZE), self._stop_event)
        token = None
        try:
            async with contextlib.aclosing(tokens), asyncio.timeout(AUTH_TIMEOUT):
                # `None` when the connection is stopped first.
                token = await anext(tokens, None)
        except (asyncio.IncompleteReadError, TimeoutError):
            pass
        except OSError as exc:
            if not is_peer_gone(exc):
                raise
        if token is not None and hmac.compare_digest(token, self.token):
            return True
        if not self._stop_event.is_set():
            logger.warning("Closing an RPC connection whose peer did not send the right token.")
        return False

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
                    response = encode_body(await task)
                    await send_stream_message(self.writer, call_id, response)
                except Exception as exc:
                    if is_peer_gone(exc):
                        # No point notifying the peer or serving this connection any further.
                        self._stop_event.set()
                        return
                    # Some other failure, e.g. an unpicklable result.
                    # Try to tell the client that no reply is coming,
                    # with the empty body of the wire format at the top of this module.
                    # Don't let a doomed notification attempt mask the original exception.
                    with suppress_peer_gone():
                        await send_stream_message(self.writer, call_id, None)
                    raise


@attrs.define(eq=False)
class _TCPAcceptor:
    """Accept connections on a listening TCP socket, instead of `asyncio.start_server`.

    The Proactor event loop of Windows closes the listening socket of `asyncio.start_server`
    when one accept fails because the peer reset its connection before it was accepted,
    after which every client is refused (CPython gh-93821, observed with 3.11.18 and 3.14.10).
    This class accepts with `loop.sock_accept()`, which leaves the listening socket open,
    and skips such a failed accept.
    On any other failure, it stops accepting, as `asyncio.start_server` does.

    Remove this class and listen with `asyncio.start_server` again
    once every supported Python version has the fix.
    It was merged into the 3.14 and 3.15 branches on 2026-11-04,
    after the releases 3.14.10 and 3.15.2, and it was not backported to 3.13 or older.
    Check then whether `tolerate_proactor_connection_errors` in `platform/asyncio.py`
    still needs to handle failed accepts.
    """

    sock: socket.socket = attrs.field()
    """The listening socket, in non-blocking mode."""

    handle: Callable[[asyncio.StreamReader, asyncio.StreamWriter], Awaitable[None]] = attrs.field()
    """The coroutine function that serves an accepted connection,
    with the same signature as the callback of `asyncio.start_server`."""

    _accept_task: asyncio.Task | None = attrs.field(init=False, default=None)
    """The task that accepts connections."""

    _connection_tasks: set[asyncio.Task] = attrs.field(init=False, factory=set)
    """The tasks that serve accepted connections, kept until they are done."""

    @classmethod
    def listen(
        cls,
        host: str,
        handle: Callable[[asyncio.StreamReader, asyncio.StreamWriter], Awaitable[None]],
    ) -> Self:
        """Listen on a free port of `host` and start accepting connections."""
        sock = socket.create_server((host, 0))
        try:
            sock.setblocking(False)
            acceptor = cls(sock, handle)
        except BaseException:
            sock.close()
            raise
        acceptor._accept_task = asyncio.create_task(acceptor._accept_loop(), name="rpc-accept")
        return acceptor

    @property
    def port(self) -> int:
        """The port on which connections are accepted."""
        return self.sock.getsockname()[1]

    async def stop(self):
        """Stop accepting connections and close the listening socket.

        The connections that were accepted already are served until they end.
        """
        if self._accept_task is not None:
            self._accept_task.cancel()
            # Unlike awaiting the task, this does not raise its cancellation in the caller.
            await asyncio.wait([self._accept_task])

    async def _accept_loop(self):
        """Accept connections and serve each of them in a task of its own."""
        loop = asyncio.get_running_loop()
        try:
            while True:
                try:
                    conn, _ = await loop.sock_accept(self.sock)
                except OSError as exc:
                    if is_aborted_accept(exc):
                        logger.debug(
                            "Skipping an accept that failed because of the connection: %r", exc
                        )
                        continue
                    loop.call_exception_handler(
                        {
                            "message": "Accept failed on a socket",
                            "exception": exc,
                            "socket": self.sock,
                        }
                    )
                    return
                task = asyncio.create_task(self._serve(conn), name="rpc-connection")
                self._connection_tasks.add(task)
                task.add_done_callback(self._connection_done)
        finally:
            self.sock.close()

    async def _serve(self, conn: socket.socket):
        """Serve one accepted connection."""
        try:
            reader, writer = await asyncio.open_connection(sock=conn)
        except BaseException:
            conn.close()
            raise
        await self.handle(reader, writer)

    def _connection_done(self, task: asyncio.Task):
        """Forget a served connection, and report how it failed, as `asyncio.start_server` does."""
        self._connection_tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            asyncio.get_running_loop().call_exception_handler(
                {
                    "message": "Unhandled exception while serving a connection",
                    "exception": task.exception(),
                    "task": task,
                }
            )


@attrs.define(eq=False)
class SocketRPCServer:
    """Accept connections on a socket and serve the RPC calls they carry.

    Instances use identity-based equality, for the same reason as `RPCServerConnection`.
    """

    handler: object = attrs.field()
    """The RPC handler."""

    socket_path: str = attrs.field()
    """The socket path at which clients find the server, see the section on endpoints.

    Nothing may exist at this path before the server starts,
    and its directory must be private, see the module docstring.
    """

    transport: str = attrs.field(validator=attrs.validators.in_(RPC_TRANSPORTS))
    """The transport to listen on, see `select_rpc_transport`."""

    _token: bytes | None = attrs.field(init=False, default=None, repr=False)
    """The token that every client must send first, `None` until it is needed."""

    _connections: set[RPCServerConnection] = attrs.field(init=False, factory=set)
    """The connections currently being served."""

    _idle: asyncio.Event = attrs.field(init=False)
    """Set when no connection is being served."""

    _stopping: bool = attrs.field(init=False, default=False)
    """Whether the server is winding down, after which it serves no new connections."""

    _stop_listening: Callable[[], Awaitable[None]] | None = attrs.field(init=False, default=None)
    """Stop accepting connections, `None` until the server listens."""

    @_idle.default
    def _default_idle(self) -> asyncio.Event:
        idle = asyncio.Event()
        idle.set()
        return idle

    async def listen(self):
        """Start accepting connections, and only then create the socket path.

        Once this returns, clients can connect and their calls are served.
        Await this before starting anything that connects to the server,
        such as a subprocess that receives the socket path,
        because a task running `serve()` may not have listened yet when that connects.
        Run `serve()` afterwards,
        which stops the server and its connections when its stop event is set.

        Raises
        ------
        ConsistencyError
            When the server listens already, which is a bug in StepUp:
            a second listener would take over the socket path
            and leave the first one running, out of reach of `serve()`.
        """
        if self._stop_listening is not None:
            raise ConsistencyError("The RPC server is listening already.")
        if self.transport == "unix":
            server = await self._listen_unix()

            async def stop_listening():
                server.close()

            self._stop_listening = stop_listening
            return
        self._token = secrets.token_bytes(TOKEN_SIZE)
        acceptor = _TCPAcceptor.listen(TCP_HOST, self._serve_connection)
        try:
            TCPEndpoint(TCP_HOST, acceptor.port, self._token).write(self.socket_path)
        except BaseException:
            await acceptor.stop()
            raise
        self._stop_listening = acceptor.stop

    async def serve(self, stop_event: asyncio.Event):
        """Accept and serve connections until `stop_event` is set.

        This first calls `listen()`, unless that was done already.
        It returns when the server has stopped serving all its connections.
        It keeps its own account of them and does not rely on `asyncio.Server.wait_closed`,
        which waits forever for a connection that the event loop failed to close.
        On Windows, a connection stays attached to its server
        when shutting down its socket fails in `_call_connection_lost` of the Proactor event loop.
        (Observed with Python 3.14.10 on Windows 11, where the shutdown of a connection
        that the peer had just closed failed once with `WinError 10022`.)

        Parameters
        ----------
        stop_event
            The server keeps accepting connections until this event is set,
            and then stops the connections that are still open.
        """
        if self._stop_listening is None:
            await self.listen()
        await stop_event.wait()
        self._stopping = True
        # Listening is stopped below, which does not end the connections being served,
        # so a connection whose peer keeps its side open keeps being served,
        # and such a peer would decide when this method returns.
        # Iterate over a copy: `_serve_connection` removes a connection when it stops serving.
        for connection in list(self._connections):
            connection.stop()
        await self._stop_listening()
        await self._idle.wait()

    async def _listen_unix(self) -> asyncio.Server:
        """Listen on a Unix domain socket that appears at the socket path once it listens."""
        # `bind()` creates the socket file, but a client that connects before `listen()` is refused.
        # The socket is therefore bound under a temporary name in the same directory,
        # and renamed to the socket path once it listens.
        # That name is kept short, because it is subject to the length limit of socket paths.
        path_tmp = Path(f"{self.socket_path}.tmp")
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server = None
        try:
            sock.bind(path_tmp)
            server = await asyncio.start_unix_server(self._serve_connection, sock=sock)
            os.replace(path_tmp, self.socket_path)
        except BaseException:
            if server is None:
                sock.close()
            else:
                server.close()
            path_tmp.remove_p()
            raise
        return server

    async def _serve_connection(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        """Serve a single accepted connection, as `asyncio.start_server` expects."""
        if self._stopping:
            # The connection was accepted just before the server stopped accepting.
            writer.close()
            return
        if self._token is not None:
            set_nodelay(writer.get_extra_info("socket"))
        connection = RPCServerConnection(self.handler, reader, writer, self._token)
        self._connections.add(connection)
        self._idle.clear()
        try:
            await connection.serve()
        finally:
            self._connections.discard(connection)
            if len(self._connections) == 0:
                self._idle.set()
