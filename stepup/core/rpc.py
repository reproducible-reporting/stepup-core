# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Lightweight and versatile RPC implementation using asyncio stream reader and writer.

This module also includes a synchronous RPC client to support simple client APIs.
"""

import asyncio
import contextlib
import importlib
import inspect
import logging
import os
import pickle
import socket
import subprocess
import sys
import traceback
from collections.abc import Awaitable, Callable, Collection
from functools import partial
from typing import Any, NoReturn

import attrs

from .asyncio import stdio, stoppable_iterator
from .exceptions import RPCError, UsageError
from .utils import is_debug

logger = logging.getLogger(__name__)

__all__ = (
    "AsyncRPCClient",
    "BaseAsyncRPCClient",
    "BaseSyncRPCClient",
    "DummyAsyncRPCClient",
    "DummySyncRPCClient",
    "SocketSyncRPCClient",
    "allow_rpc",
    "fmt_rpc_call",
    "serve_rpc",
    "serve_socket_rpc",
    "serve_stdio_rpc",
)


#
# Utilities
#


def fmt_rpc_call(name: str, args: Collection, kwargs: dict) -> str:
    """String format an RPC call with arguments."""
    all_args = [repr(arg) for arg in args] + [f"{name}={value!r}" for name, value in kwargs.items()]
    return f"{name}({', '.join(all_args)})"


@attrs.define(frozen=True)
class RemoteError:
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
    def from_exception(cls, exc: BaseException) -> "RemoteError":
        """Summarize an exception raised while handling an RPC call."""
        return cls(
            type(exc).__module__,
            type(exc).__qualname__,
            str(exc),
            "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
            isinstance(exc, UsageError),
        )


def _rebuild_exception(err: RemoteError) -> BaseException:
    """Recreate a server-side `UsageError` in the client process.

    Every step is guarded and falls back to an `RPCError` with the original message,
    so that a payload which cannot be reconstructed faithfully
    never makes the client raise something arbitrary.

    Parameters
    ----------
    err
        The error payload received from the server.

    Returns
    -------
    exc
        An instance of the original exception class,
        or an `RPCError` when the class could not be reconstructed.
        The server-side traceback is not carried over:
        `_handle_request` logs it to `.stepup/director.log`,
        which is the record to consult when it is needed.
    """
    try:
        cls = getattr(importlib.import_module(err.module), err.qualname)
    except (ImportError, AttributeError):
        # The class is not importable in the client process.
        return RPCError(err.message)
    if not (isinstance(cls, type) and issubclass(cls, UsageError)):
        # Checking `UsageError` (and not `Exception`) keeps the reconstruction inside the set
        # of classes this design vouches for: a plain-message constructor and a meaning the
        # user can act on.
        return RPCError(err.message)
    try:
        return cls(err.message)
    except TypeError:
        # A subclass with a richer constructor signature. None exist today.
        return RPCError(err.message)


def _handle_error(err: RemoteError, name: str, args, kwargs) -> NoReturn:
    """Raise a client-side exception for an RPC call that failed on the server.

    A usage error is re-raised as the class the server raised, without the server traceback,
    so that the user is confronted with their own mistake instead of StepUp's plumbing.
    Anything else indicates a bug in StepUp, for which the full traceback is what a bug
    report needs, so it is wrapped in an `RPCError` that embeds it.
    `STEPUP_DEBUG` selects the latter treatment for every exception.
    """
    if err.usage and not is_debug():
        # `from None`: a chained `RPCError` would put StepUp's plumbing back in the traceback.
        raise _rebuild_exception(err) from None
    fmt_call = fmt_rpc_call(name, args, kwargs)
    raise RPCError(
        f"An exception was raised in the server during the call {fmt_call}: "
        f"\n\n{err.traceback_text}"
    )


def allow_rpc(func):
    """Decorator to allow a function to be called remotely."""
    func._allow_rpc = True
    return func


#
# RPC message protocol
#


async def _recv_rpc_message(reader: asyncio.StreamReader) -> tuple[int, bytes] | tuple[None, None]:
    """Read a single RPC request.

    Parameters
    ----------
    reader
        The StreamReader to read the next message from.

    Returns
    -------
    call_id
        The call id of the message, used to label the response.
    body
        The content of the message. None means the RPC loops should be stopped.
        In this case, no response is expected.
    """
    try:
        call_id = int.from_bytes(await reader.readexactly(8))
        size = int.from_bytes(await reader.readexactly(8))
        body = None if size == 0 else await reader.readexactly(size)
    except (asyncio.IncompleteReadError, ConnectionError):
        # IncompleteReadError is a graceful EOF (peer closed cleanly). A reset while a read
        # is pending instead surfaces as a raw ConnectionError (e.g. ConnectionResetError).
        # Both mean the same thing here: the peer is gone, so the RPC loop should stop.
        return None, None
    return call_id, body


async def _send_rpc_message(writer: asyncio.StreamWriter, call_id: int, message: bytes | None):
    """Send a single RPC response.

    Parameters
    ----------
    writer
        The StreamWriter to write the response to.
    call_id
        The call id of the message, used to label the response.
        This must match the call id of the request to which is being responded.
    message
        The content of the message. None means the RPC loops should be stopped.
    """
    writer.write(call_id.to_bytes(8))
    if message is None:
        writer.write((0).to_bytes(8))
    else:
        writer.write(len(message).to_bytes(8))
        writer.write(message)
    await writer.drain()


#
# RPC server, always async
#


async def serve_rpc(
    handler,
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    stop_event: asyncio.Event | None = None,
):
    """Run an RPC server with async stream reader and writer until stop_event is set.

    The reader and writer must be connected to an RPC client implemented in this module.

    Parameters
    ----------
    handler
        Any object whose methods are to be called remotely.
    reader
        The RPC calls are received from this reader.
    writer
        The RPC results or exceptions are written to the writer.
    stop_event
        The RPC loops keep running until the stop event is set.
        When not given, an internal event is created and
        the client is responsible for closing the loop
    """
    if stop_event is None:
        stop_event = asyncio.Event()
    queue = asyncio.Queue()
    await asyncio.gather(
        _serve_rpc_recv_loop(handler, reader, stop_event, queue),
        _serve_rpc_send_loop(writer, stop_event, queue),
    )


async def _serve_rpc_recv_loop(
    handler, reader: asyncio.StreamReader, stop_event: asyncio.Event, queue: asyncio.Queue
):
    """Receive requests from RPC clients and create corresponding tasks."""
    tasks = set()
    si = stoppable_iterator(_recv_rpc_message, stop_event, (reader,))
    async for call_id, request in si:
        if call_id is None or request is None:
            stop_event.set()
            break
        name, args, kwargs = pickle.loads(request)
        task_name = f"RPC:{name}-{call_id}"
        task = asyncio.create_task(_handle_request(handler, name, args, kwargs), name=task_name)
        tasks.add(task)
        task.add_done_callback(partial(_queue_done, call_id, tasks, queue))


async def _handle_request(handler, name: str, args: list, kwargs: dict) -> tuple[Any, bool]:
    """Handle an RPC request from the client."""
    try:
        # print(fmt_rpc_call(name, args, kwargs))
        # Get the function, or raise RPCError
        try:
            call = getattr(handler, name)
        except AttributeError as exc:
            raise RPCError(f"Unknown remote procedure {name}") from exc
        # Is this method allowed?
        if not getattr(call, "_allow_rpc", False):
            raise RPCError(f"Remote procedure {name} exists but is not allowed")
        # Basic argument check (ignores type hints)
        signature = inspect.signature(call)
        try:
            bound = signature.bind(*args, **kwargs)
        except TypeError as exc:
            raise RPCError(f"Invalid arguments: {fmt_rpc_call(name, args, kwargs)}") from exc
        bound.apply_defaults()
        result = call(*bound.args, **bound.kwargs)
        if inspect.iscoroutinefunction(call):
            result = await result
        return result, False
    except BaseException as exc:  # noqa: BLE001
        err = RemoteError.from_exception(exc)
        # Keep a server-side record of the traceback, because the client may hide it.
        # `WARNING` and not `INFO`: the director's default log level is `WARNING`
        # (see `__main__.py`), so an `INFO` record would be dropped in exactly the case
        # this record exists for, i.e. a build without `STEPUP_DEBUG`.
        # `WARNING` and not `ERROR`: the last pattern in `DIRECTOR_LOG_CHECKS` (`utils.py`)
        # anchors on the level field, so an `ERROR` record here would turn every reported
        # usage error into a build finding.
        logger.warning(
            "Exception in RPC call %s:\n%s", fmt_rpc_call(name, args, kwargs), err.traceback_text
        )
        return err, True


def _queue_done(call_id: int, tasks: set[asyncio.Task], queue: asyncio.Queue, task: asyncio.Task):
    """Put replies of completed tasks on queue for send loop."""
    tasks.discard(task)
    queue.put_nowait((call_id, task))


async def _serve_rpc_send_loop(
    writer: asyncio.StreamWriter, stop_event: asyncio.Event, queue: asyncio.Queue
):
    """Send replies from completed tasks back to RPC client."""
    async for call_id, task in stoppable_iterator(queue.get, stop_event):
        try:
            response = pickle.dumps(await task, protocol=pickle.HIGHEST_PROTOCOL)
            await _send_rpc_message(writer, call_id, response)
        except ConnectionError:
            # The peer is already gone: no point notifying it or serving this connection
            # any further.
            stop_event.set()
            return
        except Exception:
            # Some other failure (e.g. an unpicklable result): try to tell the client, but
            # don't let a doomed notification attempt mask the original exception.
            with contextlib.suppress(ConnectionError):
                await _send_rpc_message(writer, call_id, None)
            raise


#
# Higher-level RPC server API
#


async def _handle_connection(
    handler,
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
):
    """Handle a single connection to the RPC server."""
    try:
        await serve_rpc(handler, reader, writer)
    finally:
        try:
            await writer.drain()
        except ConnectionError:
            logger.warning("Connection error while draining writer in _handle_connection")
        writer.close()
        try:
            await writer.wait_closed()
        except ConnectionError:
            logger.warning("Connection error while closing writer in _handle_connection")


async def serve_socket_rpc(handler, path: str, stop_event: asyncio.Event):
    """Serve an RPC server on a Unix domain socket.

    Parameters
    ----------
    handler
        Any object whose methods (decorated with `@allow_rpc`) are to be called remotely.
    path
        The path to the Unix domain socket.
    stop_event
        The RPC loops keep running until the stop event is set.
    """
    server = await asyncio.start_unix_server(partial(_handle_connection, handler), path)
    await stop_event.wait()
    server.close()
    await server.wait_closed()
    if sys.version_info < (3, 12, 1) and server._waiters is not None:
        # Workaround for server.wait_closed() issue fixed in Python 3.12.1
        # See https://github.com/python/cpython/issues/120866
        waiter = server.get_loop().create_future()
        server._waiters.append(waiter)
        await waiter


async def serve_stdio_rpc(handler):
    """Serve an RPC server on stdin and stdout.

    Parameters
    ----------
    handler
        Any object whose methods (decorated with `@allow_rpc`) are to be called remotely.
    """
    reader, writer = await stdio()
    await serve_rpc(handler, reader, writer)


#
# RPC Client code
#


@attrs.define
class CallInterface:
    """A proxy object to call remote functions."""

    func: Callable = attrs.field()

    def __getattr__(self, item):
        """Return a function, with a pre-filled first argument name, that calls the remote function.

        Parameters
        ----------
        item
            The name of the remote function to call.
        """
        return partial(self.func, item)


@attrs.define
class BaseAsyncRPCClient:
    """Base class for async RPC clients."""

    _call: CallInterface = attrs.field(init=False)
    """The call interface to call remote functions."""

    @_call.default
    def _default_call(self):
        return CallInterface(self)

    @property
    def call(self) -> CallInterface:
        return self._call

    async def __call__(self, name: str, *args, **kwargs) -> Any:
        """Call a function of the RPC server. This must be implemented in subclassses."""
        raise NotImplementedError


@attrs.define
class AsyncRPCClient(BaseAsyncRPCClient):
    """RPC client."""

    reader: asyncio.StreamReader = attrs.field()
    """The reader to receive responses from the server."""

    writer: asyncio.StreamWriter = attrs.field()
    """The writer to send requests to the server."""

    counter: int = attrs.field(init=False, default=0)
    """A counter to keep track of the call ids, needed to pair requests and responses."""

    _recv_events: dict[int, asyncio.Event] = attrs.field(init=False, factory=dict)
    """Events to signal when a response is received for a call id."""

    _recv_data: dict[int, bytes] = attrs.field(init=False, factory=dict)
    """The responses received from the server, indexed by call id."""

    _recv_stop: asyncio.Event = attrs.field(init=False, factory=asyncio.Event)
    """Event to signal the receive loop to stop."""

    _recv_closed: bool = attrs.field(init=False, default=False)
    """Whether the receive loop has stopped, meaning no responses can arrive anymore."""

    _recv_task: asyncio.Task = attrs.field(init=False)
    """The task running the receive loop."""

    _wait_on_close: list[Awaitable[Any]] = attrs.field(factory=list)
    """Awaitables to wait for when closing the client."""

    @_recv_task.default
    def _default_recv_task(self):
        # Keep reference to task to prevent garbage collection while client is alive.
        return asyncio.create_task(self._client_rpc_recv_loop(), name="client-rpc-recv-loop")

    async def _client_rpc_recv_loop(self):
        """Receive responses from the server and store them in the response dictionary."""
        si = stoppable_iterator(_recv_rpc_message, self._recv_stop, (self.reader,))
        async for call_id, response in si:
            if call_id is None:
                self._recv_stop.set()
                break
            self._recv_data[call_id] = response
            self._recv_events[call_id].set()
        # The peer is gone (or `close()` was called). Wake every pending caller,
        # so that it raises instead of waiting for a response that can no longer arrive.
        self._recv_closed = True
        for recv_event in self._recv_events.values():
            recv_event.set()

    @classmethod
    async def subprocess(cls, executable: str, *args, **kwargs):
        """Create an RPC client connected to a server running in a subprocess."""
        process = await asyncio.create_subprocess_exec(
            executable, *args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, **kwargs
        )
        return AsyncRPCClient(process.stdout, process.stdin, wait_on_close=[process.wait()])

    @classmethod
    async def socket(cls, path: str):
        """Create an RPC client connected to a server running on a Unix domain socket."""
        reader, writer = await asyncio.open_unix_connection(path)
        return AsyncRPCClient(reader, writer)

    async def __call__(self, name: str, *args, **kwargs):
        """Call a function of the RPC server.

        Parameters
        ----------
        name
            The name of the remote function to call
        args
            Arguments for the remote function.
        kwargs
            Keyword arguments for the remote function.

        Raises
        ------
        ConnectionResetError
            When the connection to the server is lost before the response is received,
            or when it was already lost before the call was made.

        Returns
        -------
        value
            Whatever the remote functions returns.
        """
        if self._recv_closed:
            raise ConnectionResetError(f"RPC connection lost before calling {name!r}")
        request = pickle.dumps([name, args, kwargs], protocol=pickle.HIGHEST_PROTOCOL)
        self.counter += 1
        call_id = self.counter
        recv_event = asyncio.Event()
        self._recv_events[call_id] = recv_event
        await _send_rpc_message(self.writer, call_id, request)
        await recv_event.wait()
        self._recv_events.pop(call_id)
        if call_id not in self._recv_data:
            # The receive loop woke us up without a response, i.e. the peer is gone.
            raise ConnectionResetError(f"RPC connection lost while calling {name!r}")
        response = self._recv_data.pop(call_id)
        body, is_error = pickle.loads(response)
        if is_error:
            _handle_error(body, name, args, kwargs)
        return body

    async def close(self):
        """Close the client.

        This will send a close message to the server and will wait for the receive loop to stop.
        """
        self.counter += 1
        call_id = self.counter
        try:
            await _send_rpc_message(self.writer, call_id, None)
        except ConnectionError as exc:
            logger.warning("Ignoring exception when closing RPC client: %r", exc)
        finally:
            self._recv_stop.set()
            await self._recv_task
            await asyncio.gather(*self._wait_on_close)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, tb):
        await self.close()


@attrs.define
class DummyAsyncRPCClient(BaseAsyncRPCClient):
    """Dummy RPC client. This one just prints the RPC calls instead of sending them to a server."""

    async def __call__(self, name: str, *args, **kwargs):
        """Call a function of the RPC server. See AsyncSocketRPCClient for details."""
        print(fmt_rpc_call(name, args, kwargs))


#
# Synchronous socket client for simple use cases
#


@attrs.define
class BaseSyncRPCClient:
    """Base class for synchronous RPC clients."""

    _call: CallInterface = attrs.field(init=False)
    """The call interface to call remote functions."""

    @_call.default
    def _default_call(self):
        return CallInterface(self)

    @property
    def call(self) -> CallInterface:
        return self._call

    def __call__(self, name: str, *args, _rpc_timeout: float | None = None, **kwargs):
        raise NotImplementedError


@attrs.define
class SocketSyncRPCClient(BaseSyncRPCClient):
    """Synchronous socket RPC client."""

    path: str = attrs.field()
    """The path to the Unix domain socket."""

    counter: int = attrs.field(init=False, default=0)
    """A counter to keep track of the call ids, needed to pair requests and responses."""

    _socket: socket.socket | None = attrs.field(init=False, default=None)
    """The socket to communicate with the server."""

    _partial_recv: bytes = attrs.field(init=False, default=b"")
    """The bytes received from the socket that are not yet used."""

    @property
    def socket(self):
        """Create a socket and connect to the server."""
        if self._socket is None:
            self._socket = socket.socket(socket.AF_UNIX)
            self._socket.connect(self.path)
        return self._socket

    def __call__(self, name: str, *args, _rpc_timeout: float | None = None, **kwargs) -> Any:
        """Call a function of the RPC server (always blocking).

        Parameters
        ----------
        name
            The name of the remote function to call.
        args
            Arguments for the remote function.
        _rpc_timeout
            The timeout for the remote call in seconds.
            This keyword argument is not passed to the remote procedure.
            When None (the default), the timeout is taken from the environment variable
            `STEPUP_SYNC_RPC_TIMEOUT` or set to 600 if the variable is not defined.
            A negative or zero value means that the client will wait indefinitely for
            a response to the remote procedure call.
            A `TimeoutError` will be raised when the wait time for a response from the RPC
            server exceeds a strictly positive timeout value.
        kwargs
            Keyword arguments for the remote function.

        Returns
        -------
        value
            Whatever the remote functions returns.
        """
        if name.startswith("_"):
            raise ValueError("Methods starting with underscores are not allowed.")
        if _rpc_timeout is None:
            _rpc_timeout = float(os.environ.get("STEPUP_SYNC_RPC_TIMEOUT", "600"))

        request = pickle.dumps([name, args, kwargs], protocol=pickle.HIGHEST_PROTOCOL)
        self.counter += 1
        call_id = self.counter
        self.socket.settimeout(None if _rpc_timeout <= 0 else _rpc_timeout)
        self._send_rpc_message(call_id, request)
        response = self._recv_rpc_message(call_id)
        body, is_error = pickle.loads(response)
        if is_error:
            _handle_error(body, name, args, kwargs)
        return body

    def close(self):
        """Close the client.

        This will send a close message to the server, after which the server should stop eventually.
        """
        self.counter += 1
        call_id = self.counter
        self._send_rpc_message(call_id, None)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, tb):
        self.close()

    def _send_rpc_message(self, call_id: int, message: bytes | None):
        """Send a single RPC request."""
        self.socket.sendall(call_id.to_bytes(8))
        if message is None:
            self.socket.sendall((0).to_bytes(8))
        else:
            self.socket.sendall(len(message).to_bytes(8))
            self.socket.sendall(message)

    def _recv_rpc_message(self, expected_call_id: int) -> bytes:
        """Receive a single RPC response."""
        call_id = int.from_bytes(self._readexactly(8))
        if call_id != expected_call_id:
            raise ValueError(f"Expected call_id {expected_call_id}, got {call_id}")
        size = int.from_bytes(self._readexactly(8))
        if size == 0:
            raise ValueError("RPC clients should never receive a closing message.")
        return self._readexactly(size)

    def _readexactly(self, size: int) -> bytes:
        """Keep reading from the socket until (at least) size bytes were received.

        Parameters
        ----------
        size
            The length of the byte sequence to receive.

        Raises
        ------
        ConnectionResetError
            When the socket returns zero bytes, the connection is lost and this error is raised.

        Returns
        -------
        data
            The bytes read from the socket of the requested size.
            Any additional data received from the socket is stored for the
            following call to `_readexactly`.
        """
        while len(self._partial_recv) < size:
            fragment = self.socket.recv(4096)
            if len(fragment) == 0:
                raise ConnectionResetError
            self._partial_recv += fragment
        result = self._partial_recv[:size]
        self._partial_recv = self._partial_recv[size:]
        return result


@attrs.define
class DummySyncRPCClient(BaseSyncRPCClient):
    """Dummy RPC client. This one just prints the RPC calls instead of sending them to a server."""

    def __call__(self, name: str, *args, _rpc_timeout: float | None = None, **kwargs) -> Any:
        """Call a function of the RPC server. See SocketSyncRPCClient for details."""
        print(fmt_rpc_call(name, args, kwargs))
