# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Unit tests for stepup.core.rpc"""

import asyncio
import contextlib
import sys

import pytest
import pytest_asyncio
from core_common import EchoHandler
from path import Path

from stepup.core.asyncio import pipe
from stepup.core.exceptions import RPCError
from stepup.core.rpc import (
    AsyncRPCClient,
    SocketSyncRPCClient,
    _handle_connection,
    _recv_rpc_message,
    _serve_rpc_send_loop,
    fmt_rpc_call,
    serve_rpc,
)


@pytest_asyncio.fixture()
async def pc():
    async with asyncio.timeout(5):
        handler = EchoHandler("pipe")
        async with pipe() as (sr, cw), pipe() as (cr, sw):
            stop_event = asyncio.Event()
            server = asyncio.create_task(serve_rpc(handler, sr, sw, stop_event))
            server.add_done_callback(lambda task: task.result())
            try:
                async with AsyncRPCClient(cr, cw) as client:
                    yield client
            finally:
                await server
                assert stop_event.is_set()


@pytest_asyncio.fixture()
async def ic():
    async with asyncio.timeout(5):
        async with await AsyncRPCClient.subprocess(
            sys.executable, Path(__file__).parent / "echo_server_stdio.py"
        ) as client:
            yield client


@pytest_asyncio.fixture()
async def socket_server_path(path_tmp):
    async with asyncio.timeout(5):
        path = path_tmp / "socket"
        process = await asyncio.create_subprocess_exec(
            sys.executable, Path(__file__).parent / "echo_server_socket.py", path
        )
        while not path.exists():
            await asyncio.sleep(0.1)
        try:
            yield path
        finally:
            reader, writer = await asyncio.open_unix_connection(path)
            async with AsyncRPCClient(reader, writer) as client:
                await client("shutdown")
            await process.wait()


@pytest_asyncio.fixture()
async def sc(socket_server_path):
    async with await AsyncRPCClient.socket(socket_server_path) as client:
        yield client


async def test_pipe_simple_args(pc):
    assert await pc.call.echo("hello") == "pipe: hello"


async def test_stdio_simple_args(ic):
    assert await ic.call.echo("hello") == "stdio: hello"


# See also https://github.com/python/cpython/issues/113538
REASON_SOCKET = "sockets hang in 3.11"


async def test_socket_simple_args(sc):
    assert await sc.call.echo("hello") == "socket: hello"


async def test_pipe_simple_kwargs(pc):
    assert await pc.call.echo(msg="hello") == "pipe: hello"


async def test_stdio_simple_kwargs(ic):
    assert await ic.call.echo(msg="hello") == "stdio: hello"


async def test_socket_simple_kwargs(sc):
    assert await sc.call.echo(msg="hello") == "socket: hello"


LCG_CASES = [
    ((1, 71, 45, 91), {}, 65),
    ((1, 71, 45), {}, 65),
    ((1, 71), {}, 65),
    ((1,), {}, 65),
    ((1,), {"multiplier": 45}, 65),
    ((1, 71, 32), {}, 52),
    ((1,), {"multiplier": 32}, 52),
]


@pytest.mark.parametrize(("args", "kwargs", "result"), LCG_CASES)
async def test_pipe_lcg_kwargs(pc, args, kwargs, result):
    assert await pc.call.lcg(*args, **kwargs) == result


@pytest.mark.parametrize(("args", "kwargs", "result"), LCG_CASES)
async def test_stdio_lcg_kwargs(ic, args, kwargs, result):
    assert await ic.call.lcg(*args, **kwargs) == result


@pytest.mark.parametrize(("args", "kwargs", "result"), LCG_CASES)
async def test_socket_lcg_kwargs(sc, args, kwargs, result):
    assert await sc.call.lcg(*args, **kwargs) == result


async def test_pipe_seq(pc):
    assert await pc.call.echo("hello", 0.1) == "pipe: hello"
    assert await pc.call.echo("world") == "pipe: world"


async def test_stdio_seq(ic):
    assert await ic.call.echo("hello", 0.1) == "stdio: hello"
    assert await ic.call.echo("world") == "stdio: world"


async def test_socket_seq(sc):
    assert await sc.call.echo("hello", 0.1) == "socket: hello"
    assert await sc.call.echo("world") == "socket: world"


async def test_pipe_par1(pc):
    expected = ["pipe: hello", "pipe: world"]
    assert await asyncio.gather(pc.call.echo("hello", 0.1), pc.call.echo("world")) == expected


async def test_stdio_par1(ic):
    expected = ["stdio: hello", "stdio: world"]
    assert await asyncio.gather(ic.call.echo("hello", 0.1), ic.call.echo("world")) == expected


async def test_socket_par1(sc):
    expected = ["socket: hello", "socket: world"]
    assert await asyncio.gather(sc.call.echo("hello", 0.1), sc.call.echo("world")) == expected


async def test_pipe_par2(pc):
    expected = ["pipe: hello", "pipe: world"]
    assert await asyncio.gather(pc.call.echo("hello"), pc.call.echo("world", 0.1)) == expected


async def test_stdio_par2(ic):
    expected = ["stdio: hello", "stdio: world"]
    assert await asyncio.gather(ic.call.echo("hello"), ic.call.echo("world", 0.1)) == expected


async def test_socket_par2(sc):
    expected = ["socket: hello", "socket: world"]
    assert await asyncio.gather(sc.call.echo("hello"), sc.call.echo("world", 0.1)) == expected


async def test_socket_multi_clients(socket_server_path):
    r1, w1 = await asyncio.open_unix_connection(socket_server_path)
    async with AsyncRPCClient(r1, w1) as c1:
        r2, w2 = await asyncio.open_unix_connection(socket_server_path)
        async with AsyncRPCClient(r2, w2) as c2:
            assert await c1.call.echo("hello", 0.1) == "socket: hello"
            assert await c2.call.echo("world") == "socket: world"
            expected = ["socket: h", "socket: w"]
            assert await asyncio.gather(c1.call.echo("h", 0.1), c2.call.echo("w")) == expected
            assert await asyncio.gather(c1.call.echo("h"), c2.call.echo("w", 0.1)) == expected
            assert await asyncio.gather(c2.call.echo("h", 0.1), c1.call.echo("w")) == expected
            assert await asyncio.gather(c2.call.echo("h"), c1.call.echo("w", 0.1)) == expected


def test_sync_socket_rpc_client(socket_server_path):
    with SocketSyncRPCClient(socket_server_path) as client:
        assert client.call.echo("hello", _rpc_timeout=5) == "socket: hello"
        assert client.call.echo("world", _rpc_timeout=5) == "socket: world"
        assert client.call.lcg(1, multiplier=32, _rpc_timeout=5) == 52
        with pytest.raises(TimeoutError):
            client.call.echo("hello", delay=0.5, _rpc_timeout=0.1)


@pytest.mark.parametrize(
    ("name", "args", "kwargs", "result"),
    [
        ("foo", ["gg", 1], {}, "foo('gg', 1)"),
        ("bar", [], {"a": 1, "b": [3, 4, "qq"]}, "bar(a=1, b=[3, 4, 'qq'])"),
        ("none", [], {}, "none()"),
        ("mixed", [()], {"_q": 5}, "mixed((), _q=5)"),
    ],
)
def test_fmt_rpc_call(name, args, kwargs, result):
    assert fmt_rpc_call(name, args, kwargs) == result


def test_fmt_rpc_call_noargs():
    assert fmt_rpc_call("foo", [], {}) == "foo()"


async def test_pipe_not_allowed(pc):
    with pytest.raises(RPCError):
        await pc.call.not_allowed()


async def test_pipe_not_defined(pc):
    with pytest.raises(RPCError):
        await pc.call.not_defined()


class _ImmediateEOFReader:
    """Reader stub that reports EOF on the first read, like an already-closed connection."""

    async def readexactly(self, n: int):
        raise asyncio.IncompleteReadError(partial=b"", expected=n)


class _ResetOnCloseWriter:
    """Writer stub whose `drain()` and `wait_closed()` both raise, like a peer that has
    already reset the connection (e.g. a forkserver child crashing right after its
    automatic `amend()` call) by the time the server tears the connection down.
    """

    def __init__(self):
        self.closed = False

    async def drain(self):
        raise ConnectionError("Connection reset by peer (drain)")

    def close(self):
        self.closed = True

    async def wait_closed(self):
        raise ConnectionResetError("Connection reset by peer (wait_closed)")


async def test_handle_connection_survives_reset_during_close():
    """A connection reset can surface on `wait_closed()`, not just `drain()`.

    Both must be tolerated, or an unhandled `ConnectionResetError` propagates out of the
    task spawned for this connection (visible as "Unhandled exception in
    client_connected_cb" in `serve_socket_rpc`).
    """
    async with asyncio.timeout(5):
        writer = _ResetOnCloseWriter()
        await _handle_connection(EchoHandler("x"), _ImmediateEOFReader(), writer)
        assert writer.closed


class _ResetReader:
    """Reader stub whose read raises a raw `ConnectionResetError`, like a peer that reset
    the connection instead of closing it cleanly (no `IncompleteReadError` involved).
    """

    async def readexactly(self, n: int):
        raise ConnectionResetError("Connection reset by peer")


class _BenignWriter:
    """Writer stub whose send/close operations always succeed."""

    def __init__(self):
        self.closed = False

    async def drain(self):
        pass

    def close(self):
        self.closed = True

    async def wait_closed(self):
        pass


async def test_recv_rpc_message_treats_reset_like_eof():
    """A reset connection must be treated the same as a clean EOF.

    `asyncio.StreamReader.readexactly()` raises `IncompleteReadError` on a clean EOF, but a
    genuine reset while a read is pending surfaces as a raw `ConnectionResetError` instead
    (the stream's stored transport exception is raised directly). `_recv_rpc_message` only
    catches `IncompleteReadError`, so a reset currently escapes uncaught instead of being
    reported as "the peer is gone" like a clean disconnect.
    """
    async with asyncio.timeout(5):
        call_id, body = await _recv_rpc_message(_ResetReader())
        assert call_id is None
        assert body is None


async def test_handle_connection_survives_reset_during_recv():
    """Same failure mode as above, exercised through the full `_handle_connection`."""
    async with asyncio.timeout(5):
        await _handle_connection(EchoHandler("x"), _ResetReader(), _BenignWriter())


class _AlwaysResetWriter:
    """Writer stub that fails every send, like a peer that is already gone."""

    def write(self, data):
        pass

    async def drain(self):
        raise ConnectionResetError("Connection reset by peer")


class _MarkerError(Exception):
    """Distinct exception, used to tell 'the original failure' apart from a masking one."""


async def _boom():
    raise _MarkerError("boom")


async def _return(value):
    return value


async def test_send_loop_stops_gracefully_on_connection_reset():
    """When the client is already gone, the send loop must not crash the connection task.

    The current bare `except:` in `_serve_rpc_send_loop` retries the send unconditionally;
    when the connection is dead, that retry also raises, and the resulting
    `ConnectionResetError` escapes uncaught (same "Unhandled exception in
    client_connected_cb" symptom as the other tests in this module).
    """
    async with asyncio.timeout(5):
        stop_event = asyncio.Event()
        queue = asyncio.Queue()
        await queue.put((1, asyncio.ensure_future(_return("ok"))))
        await _serve_rpc_send_loop(_AlwaysResetWriter(), stop_event, queue)
        assert stop_event.is_set()


async def test_send_loop_does_not_mask_original_error_with_connection_error():
    """A genuine handler-side failure must surface as itself, not as a `ConnectionError`.

    If the connection also happens to be dead, the loop's own attempt to notify the
    (already gone) client of the failure must not replace the original exception with the
    `ConnectionError` from that doomed notification attempt.
    """
    async with asyncio.timeout(5):
        stop_event = asyncio.Event()
        queue = asyncio.Queue()
        await queue.put((1, asyncio.ensure_future(_boom())))
        with pytest.raises(_MarkerError):
            await _serve_rpc_send_loop(_AlwaysResetWriter(), stop_event, queue)


@pytest_asyncio.fixture()
async def vanishing_server_path(path_tmp):
    """Path of a socket server that drops the connection instead of answering the first call."""
    path = path_tmp / "vanishing_socket"

    async def handle(reader, writer):
        # Read the fixed-size header of the first request and then disappear,
        # like a director that dies while a call is in flight.
        with contextlib.suppress(asyncio.IncompleteReadError, ConnectionError):
            await reader.readexactly(16)
        writer.close()

    server = await asyncio.start_unix_server(handle, path)
    try:
        yield path
    finally:
        server.close()
        await server.wait_closed()


async def test_client_call_raises_when_server_dies(vanishing_server_path):
    """A call in flight must fail when the peer disappears, instead of waiting forever.

    Only the receive loop can set the per-call event, so a peer that goes away without
    answering leaves the caller waiting for a response that can never arrive.
    """
    async with asyncio.timeout(5):
        client = await AsyncRPCClient.socket(vanishing_server_path)
        with pytest.raises(ConnectionResetError):
            await client.call.echo("hello")
        await client.close()


async def test_client_close_after_peer_gone(vanishing_server_path):
    """Closing a client whose peer is already gone must complete without hanging or raising."""
    async with asyncio.timeout(5):
        client = await AsyncRPCClient.socket(vanishing_server_path)
        with pytest.raises(ConnectionResetError):
            await client.call.echo("hello")
        await client.close()
        # A new call cannot be answered either, so it must fail instead of waiting forever.
        with pytest.raises(ConnectionResetError):
            await client.call.echo("world")
