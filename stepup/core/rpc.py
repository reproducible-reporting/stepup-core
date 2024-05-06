# StepUp Core provides the basic framework for the StepUp build tool.
# Copyright (C) 2024 Toon Verstraelen
#
# This file is part of StepUp Core.
#
# StepUp Core is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 3
# of the License, or (at your option) any later version.
#
# StepUp Core is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, see <http://www.gnu.org/licenses/>
#
# --
"""Lightweight and versatile RPC implementation using asyncio stream reader and writer.

This module also includes a synchronous RPC client to support simple client APIs.
"""

import asyncio
import inspect
import pickle
import socket
import subprocess
import traceback
from collections.abc import Awaitable, Callable, Collection
from functools import partial
from typing import Any

import attrs

from .asyncio import stdio, stoppable_iterator
from .exceptions import RPCError

__all__ = (
    "fmt_rpc_call",
    "allow_rpc",
    "serve_rpc",
    "serve_socket_rpc",
    "serve_stdio_rpc",
    "BaseAsyncRPCClient",
    "AsyncRPCClient",
    "DummyAsyncRPCClient",
    "BaseSyncRPCClient",
    "SocketSyncRPCClient",
    "DummySyncRPCClient",
)


#
# Utilities
#


def fmt_rpc_call(name: str, args: Collection, kwargs: dict) -> str:
    """String format an RPC call with arguments."""
    all_args = [repr(arg) for arg in args] + [f"{name}={value!r}" for name, value in kwargs.items()]
    return f"{name}({', '.join(all_args)})"


def _handle_error(body: str, name: str, args, kwargs):
    fmt_call = fmt_rpc_call(name, args, kwargs)
    raise RPCError(f"An exception was raised in the server during the call {fmt_call}: \n\n{body}")


def allow_rpc(func):
    func._allow_rpc = True
    return func


#
# RPC message protocol
#


async def _recv_rpc_message(reader: asyncio.StreamReader) -> tuple[int | None, bytes | None]:
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
        if size == 0:
            body = None
        else:
            body = await reader.readexactly(size)
        return call_id, body
    except asyncio.IncompleteReadError:
        return None, None


async def _send_rpc_message(writer: asyncio.StreamWriter, call_id: int, message: bytes | None):
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
        else:
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
        if asyncio.iscoroutinefunction(call):
            result = await result
        return result, False
    except Exception as exc:
        message = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        return message, True


def _queue_done(call_id: int, tasks: set[asyncio.Task], queue: asyncio.Queue, task: asyncio.Task):
    """Put replies of completed tasks on queue for send loop."""
    tasks.discard(task)
    queue.put_nowait((call_id, task))


async def _serve_rpc_send_loop(
    writer: asyncio.StreamWriter, stop_event: asyncio.Event, queue: asyncio.Queue
):
    """Send replies from completed tasks back to RPC client."""
    async for call_id, task in stoppable_iterator(queue.get, stop_event):
        response = pickle.dumps(await task, protocol=pickle.HIGHEST_PROTOCOL)
        await _send_rpc_message(writer, call_id, response)


#
# Higher-level RPC server API
#


async def _handle_connection(handler, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    await serve_rpc(handler, reader, writer)
    writer.close()
    await writer.wait_closed()


async def serve_socket_rpc(handler, path, stop_event):
    server = await asyncio.start_unix_server(partial(_handle_connection, handler), path)
    async with server:
        await stop_event.wait()


async def serve_stdio_rpc(handler):
    reader, writer = await stdio()
    await serve_rpc(handler, reader, writer)


#
# RPC Client code
#


@attrs.define
class CallInterface:
    func: Callable = attrs.field()

    def __getattr__(self, item):
        return partial(self.func, item)


@attrs.define
class BaseAsyncRPCClient:
    _call: CallInterface = attrs.field(init=False)

    @_call.default
    def _default_call(self):
        return CallInterface(self)

    @property
    def call(self) -> CallInterface:
        return self._call

    async def __call__(self, name: str, *args, **kwargs):
        raise NotImplementedError


@attrs.define
class AsyncRPCClient(BaseAsyncRPCClient):
    """RPC client."""

    reader: asyncio.StreamReader = attrs.field()
    writer: asyncio.StreamWriter = attrs.field()
    counter: int = attrs.field(init=False, default=0)
    _recv_events: dict[int, asyncio.Event] = attrs.field(init=False, factory=dict)
    _recv_data: dict[int, bytes] = attrs.field(init=False, factory=dict)
    _recv_stop: asyncio.Event = attrs.field(init=False, factory=asyncio.Event)
    _recv_task: asyncio.Task = attrs.field(init=False)
    _wait_on_close: list[Awaitable[Any]] = attrs.field(factory=list)

    @_recv_task.default
    def _default_recv_task(self):
        task = asyncio.create_task(self._client_rpc_recv_loop(), name="client-rpc-recv-loop")
        # Store reference to task to prevent garbage collection while client is alive.
        return task

    async def _client_rpc_recv_loop(self):
        si = stoppable_iterator(_recv_rpc_message, self._recv_stop, (self.reader,))
        async for call_id, response in si:
            if call_id is None:
                self._recv_stop.set()
                break
            self._recv_data[call_id] = response
            self._recv_events[call_id].set()

    @classmethod
    async def subprocess(cls, executable: str, *args, **kwargs):
        process = await asyncio.create_subprocess_exec(
            executable, *args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, **kwargs
        )
        return AsyncRPCClient(process.stdout, process.stdin, wait_on_close=[process.wait()])

    @classmethod
    async def socket(cls, path: str):
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

        Returns
        -------
        value
            Whatever the remote functions returns.
        """
        request = pickle.dumps([name, args, kwargs], protocol=pickle.HIGHEST_PROTOCOL)
        self.counter += 1
        call_id = self.counter
        recv_event = asyncio.Event()
        self._recv_events[call_id] = recv_event
        await _send_rpc_message(self.writer, call_id, request)
        await recv_event.wait()
        response = self._recv_data.pop(call_id)
        self._recv_events.pop(call_id)
        body, is_error = pickle.loads(response)
        if is_error:
            _handle_error(body, name, args, kwargs)
        return body

    async def close(self):
        self.counter += 1
        call_id = self.counter
        await _send_rpc_message(self.writer, call_id, None)
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

    async def __call__(self, name: str, *args, _rpc_timeout: float | None = None, **kwargs):
        """Call a function of the RPC server. See AsyncSocketRPCClient for details."""
        print(fmt_rpc_call(name, args, kwargs))


#
# Synchronous socket client for simple use cases
#


@attrs.define
class BaseSyncRPCClient:
    _call: CallInterface = attrs.field(init=False)

    @_call.default
    def _default_call(self):
        return CallInterface(self)

    @property
    def call(self) -> CallInterface:
        return self._call

    def __call__(self, name: str, *args, _rpc_timeout: float | None = 5.0, **kwargs):
        raise NotImplementedError


@attrs.define
class SocketSyncRPCClient(BaseSyncRPCClient):
    path: str = attrs.field()
    counter: int = attrs.field(init=False, default=0)
    _socket: socket.socket = attrs.field(init=False)
    _partial_recv: bytes = attrs.field(init=False, default=b"")

    @_socket.default
    def _default_socket(self):
        result = socket.socket(socket.AF_UNIX)
        result.connect(self.path)
        return result

    @property
    def call(self) -> CallInterface:
        return self._call

    def __call__(self, name: str, *args, _rpc_timeout: float | None = 5.0, **kwargs):
        """Call a function of the RPC server.

        Parameters
        ----------
        name
            The name of the remote function to call
        args
            Arguments for the remote function.
        _rpc_timeout
            This keyword argument is not passed to the remote procedure.
            When None (the default), the client will wait
            indefinitely for a response to the remote call.
            Otherwise, the timeout is in seconds and must be strictly positive.
            A TimeoutError will be raised when communication with the remote exceeds the timeout.
        kwargs
            Keyword arguments for the remote function.

        Returns
        -------
        value
            Whatever the remote functions returns.
        """
        if name.startswith("_"):
            raise ValueError("Methods starting with underscores are not allowed.")
        if not (_rpc_timeout is None or _rpc_timeout > 0):
            raise ValueError("The timeout must be None or strictly positive.")

        request = pickle.dumps([name, args, kwargs], protocol=pickle.HIGHEST_PROTOCOL)
        self.counter += 1
        call_id = self.counter
        self._socket.settimeout(_rpc_timeout)
        self._send_rpc_message(call_id, request)
        response = self._recv_rpc_message(call_id)
        body, is_error = pickle.loads(response)
        if is_error:
            _handle_error(body, name, args, kwargs)
        return body

    def close(self):
        self.counter += 1
        call_id = self.counter
        self._send_rpc_message(call_id, None)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, tb):
        self.close()

    def _send_rpc_message(self, call_id: int, message: bytes | None):
        self._socket.sendall(call_id.to_bytes(8))
        if message is None:
            self._socket.sendall((0).to_bytes(8))
        else:
            self._socket.sendall(len(message).to_bytes(8))
            self._socket.sendall(message)

    def _recv_rpc_message(self, expected_call_id: int) -> bytes:
        call_id = int.from_bytes(self._readexactly(8))
        if call_id != expected_call_id:
            raise ValueError(f"Expected call_id {expected_call_id}, got {call_id}")
        size = int.from_bytes(self._readexactly(8))
        if size == 0:
            raise ValueError("RPC clients should never receive a closing message.")
        body = self._readexactly(size)
        return body

    def _readexactly(self, size: int) -> bytes:
        """Keep reading from the socket until (at least) size bytes were received.

        Parameters
        ----------
        size
            The length of the byte sequence to receive.

        Raises
        ------
        ConectionResetError
            When the socket returns zero bytes, the connection is lost and this error is raised.

        Returns
        -------
        data
            The bytes read from the socket of the requested size.
            Any additional data received from the socket is stored for the
            following call to `_readexactly`.
        """
        while len(self._partial_recv) < size:
            fragment = self._socket.recv(4096)
            if len(fragment) == 0:
                raise ConnectionResetError()
            self._partial_recv += fragment
        result = self._partial_recv[:size]
        self._partial_recv = self._partial_recv[size:]
        return result


@attrs.define
class DummySyncRPCClient(BaseSyncRPCClient):
    """Dummy RPC client. This one just prints the RPC calls instead of sending them to a server."""

    def __call__(self, name: str, *args, _rpc_timeout: float | None = None, **kwargs):
        """Call a function of the RPC server. See SocketSyncRPCClient for details."""
        print(fmt_rpc_call(name, args, kwargs))
