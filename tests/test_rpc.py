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
"""Unit tests for stepup.core.rpc"""

import asyncio
import sys

import pytest
import pytest_asyncio
from core_common import EchoHandler
from path import Path

from stepup.core.asyncio import pipe
from stepup.core.exceptions import RPCError
from stepup.core.rpc import AsyncRPCClient, SocketSyncRPCClient, fmt_rpc_call, serve_rpc


@pytest_asyncio.fixture()
async def pc():
    async with asyncio.timeout(5):
        handler = EchoHandler("pipe")
        sr, cw = await pipe()
        cr, sw = await pipe()
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


@pytest.mark.asyncio
async def test_pipe_simple_args(pc):
    assert await pc.call.echo("hello") == "pipe: hello"


@pytest.mark.asyncio
async def test_stdio_simple_args(ic):
    assert await ic.call.echo("hello") == "stdio: hello"


@pytest.mark.asyncio
async def test_socket_simple_args(sc):
    assert await sc.call.echo("hello") == "socket: hello"


@pytest.mark.asyncio
async def test_pipe_simple_kwargs(pc):
    assert await pc.call.echo(msg="hello") == "pipe: hello"


@pytest.mark.asyncio
async def test_stdio_simple_kwargs(ic):
    assert await ic.call.echo(msg="hello") == "stdio: hello"


@pytest.mark.asyncio
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


@pytest.mark.asyncio
@pytest.mark.parametrize("args, kwargs, result", LCG_CASES)
async def test_pipe_lcg_kwargs(pc, args, kwargs, result):
    assert await pc.call.lcg(*args, **kwargs) == result


@pytest.mark.asyncio
@pytest.mark.parametrize("args, kwargs, result", LCG_CASES)
async def test_stdio_lcg_kwargs(ic, args, kwargs, result):
    assert await ic.call.lcg(*args, **kwargs) == result


@pytest.mark.asyncio
@pytest.mark.parametrize("args, kwargs, result", LCG_CASES)
async def test_socket_lcg_kwargs(sc, args, kwargs, result):
    assert await sc.call.lcg(*args, **kwargs) == result


@pytest.mark.asyncio
async def test_pipe_seq(pc):
    assert await pc.call.echo("hello", 0.1) == "pipe: hello"
    assert await pc.call.echo("world") == "pipe: world"


@pytest.mark.asyncio
async def test_stdio_seq(ic):
    assert await ic.call.echo("hello", 0.1) == "stdio: hello"
    assert await ic.call.echo("world") == "stdio: world"


@pytest.mark.asyncio
async def test_socket_seq(sc):
    assert await sc.call.echo("hello", 0.1) == "socket: hello"
    assert await sc.call.echo("world") == "socket: world"


@pytest.mark.asyncio
async def test_pipe_par1(pc):
    expected = ["pipe: hello", "pipe: world"]
    assert await asyncio.gather(pc.call.echo("hello", 0.1), pc.call.echo("world")) == expected


@pytest.mark.asyncio
async def test_stdio_par1(ic):
    expected = ["stdio: hello", "stdio: world"]
    assert await asyncio.gather(ic.call.echo("hello", 0.1), ic.call.echo("world")) == expected


@pytest.mark.asyncio
async def test_socket_par1(sc):
    expected = ["socket: hello", "socket: world"]
    assert await asyncio.gather(sc.call.echo("hello", 0.1), sc.call.echo("world")) == expected


@pytest.mark.asyncio
async def test_pipe_par2(pc):
    expected = ["pipe: hello", "pipe: world"]
    assert await asyncio.gather(pc.call.echo("hello"), pc.call.echo("world", 0.1)) == expected


@pytest.mark.asyncio
async def test_stdio_par2(ic):
    expected = ["stdio: hello", "stdio: world"]
    assert await asyncio.gather(ic.call.echo("hello"), ic.call.echo("world", 0.1)) == expected


@pytest.mark.asyncio
async def test_socket_par2(sc):
    expected = ["socket: hello", "socket: world"]
    assert await asyncio.gather(sc.call.echo("hello"), sc.call.echo("world", 0.1)) == expected


@pytest.mark.asyncio
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
    "name, args, kwargs, result",
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


@pytest.mark.asyncio
async def test_pipe_not_allowed(pc):
    with pytest.raises(RPCError):
        assert await pc.call.not_allowed()


@pytest.mark.asyncio
async def test_pipe_not_defined(pc):
    with pytest.raises(RPCError):
        assert await pc.call.not_defined()
