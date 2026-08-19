# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Unit tests for stepup.core.rpc"""

import asyncio
import contextlib
import copy
import logging
import pickle
import sys

import pytest
import pytest_asyncio
from core_common import EchoHandler, settled_task_names
from path import Path

from stepup.core.exceptions import (
    ConfigError,
    CyclicError,
    RPCClientUnusableError,
    RPCError,
    UsageError,
)
from stepup.core.rpc import (
    CLOSE_TIMEOUT,
    FIELD_SIZE,
    HEADER_SIZE,
    MAX_BODY_SIZE,
    NO_RPC_TIMEOUT,
    DummySyncRPCClient,
    RemoteCallProxy,
    RemoteFailure,
    RPCCall,
    RPCServerConnection,
    SocketAsyncRPCClient,
    SocketRPCServer,
    SocketSyncRPCClient,
    _call_and_capture_failure,
    _decode_header,
    _decode_request,
    _decode_response,
    _default_sync_rpc_timeout,
    _encode_body,
    _encode_message,
    _raise_remote_error,
    _recv_stream_message,
    _resolve_socket_timeout,
    _SocketReader,
    allow_rpc,
    is_rpc_allowed,
)
from stepup.core.utils import DIRECTOR_LOG_CHECKS


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
            async with SocketAsyncRPCClient(path) as client:
                await client("shutdown")
            await process.wait()


@pytest_asyncio.fixture()
async def sc(socket_server_path):
    async with SocketAsyncRPCClient(socket_server_path) as client:
        yield client


async def test_socket_simple_args(sc):
    assert await sc.call.echo("hello") == "socket: hello"


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
async def test_socket_lcg_kwargs(sc, args, kwargs, result):
    assert await sc.call.lcg(*args, **kwargs) == result


async def test_socket_seq(sc):
    assert await sc.call.echo("hello", 0.1) == "socket: hello"
    assert await sc.call.echo("world") == "socket: world"


async def test_socket_par1(sc):
    expected = ["socket: hello", "socket: world"]
    assert await asyncio.gather(sc.call.echo("hello", 0.1), sc.call.echo("world")) == expected


async def test_socket_par2(sc):
    expected = ["socket: hello", "socket: world"]
    assert await asyncio.gather(sc.call.echo("hello"), sc.call.echo("world", 0.1)) == expected


async def test_socket_multi_clients(socket_server_path):
    async with (
        SocketAsyncRPCClient(socket_server_path) as c1,
        SocketAsyncRPCClient(socket_server_path) as c2,
    ):
        assert await c1.call.echo("hello", 0.1) == "socket: hello"
        assert await c2.call.echo("world") == "socket: world"
        expected = ["socket: h", "socket: w"]
        assert await asyncio.gather(c1.call.echo("h", 0.1), c2.call.echo("w")) == expected
        assert await asyncio.gather(c1.call.echo("h"), c2.call.echo("w", 0.1)) == expected
        assert await asyncio.gather(c2.call.echo("h", 0.1), c1.call.echo("w")) == expected
        assert await asyncio.gather(c2.call.echo("h"), c1.call.echo("w", 0.1)) == expected


async def test_serve_stops_an_idle_connection(path_tmp):
    """A peer that leaves a connection open may not delay the shutdown of the server.

    The client here stays connected on purpose and never closes,
    which is the state a step process or a `stepup` tool is in between two calls.
    Without the server stopping such a connection itself,
    `SocketRPCServer.serve` waits for the peer and never returns.
    """
    async with asyncio.timeout(5):
        socket_path = path_tmp / "socket"
        stop_event = asyncio.Event()
        server_task = asyncio.create_task(
            SocketRPCServer(EchoHandler("idle"), str(socket_path)).serve(stop_event)
        )
        client = SocketAsyncRPCClient(socket_path)
        try:
            while not socket_path.exists():
                await asyncio.sleep(0.01)
            assert await client.call.echo("hello") == "idle: hello"
            stop_event.set()
            await server_task
        finally:
            # The server closed this connection first, which `close` must accept.
            await client.close()


def test_sync_socket_rpc_client(socket_server_path):
    with SocketSyncRPCClient(socket_server_path) as client:
        assert client.call.echo("hello", _rpc_timeout=5) == "socket: hello"
        assert client.call.echo("world", _rpc_timeout=5) == "socket: world"
        assert client.call.lcg(1, multiplier=32, _rpc_timeout=5) == 52
        with pytest.raises(TimeoutError):
            client.call.echo("hello", delay=0.5, _rpc_timeout=0.1)


def test_sync_client_is_broken_after_a_timeout(socket_server_path):
    """A timeout leaves the response on its way, so the client must refuse to send anything else.

    The response of the timed out call would otherwise be read back as the response of the
    next one, which is worse than not being able to talk to the director at all.
    """
    with SocketSyncRPCClient(socket_server_path) as client:
        with pytest.raises(TimeoutError):
            client.call.echo("hello", delay=0.5, _rpc_timeout=0.1)
        with pytest.raises(RPCClientUnusableError):
            client.call.echo("world", _rpc_timeout=5)


#
# Closing a client.
#


async def test_client_close_closes_the_connection(socket_server_path):
    """A closed client must not leave its end of the connection open."""
    async with asyncio.timeout(5):
        client = SocketAsyncRPCClient(socket_server_path)
        assert await client.call.echo("hello") == "socket: hello"
        await client.close()
        assert client._writer.is_closing()


async def test_client_close_is_idempotent(socket_server_path):
    """An explicit close inside an `async with` block must not send a second close message."""
    async with asyncio.timeout(5):
        async with SocketAsyncRPCClient(socket_server_path) as client:
            await client.close()
            counter = client._counter
        assert client._counter == counter


async def test_client_close_without_connecting(path_tmp):
    """Closing a client that was never used must not connect just to say goodbye.

    There is no server at this path, so an attempt to connect fails loudly.
    """
    async with asyncio.timeout(5):
        client = SocketAsyncRPCClient(path_tmp / "never_created_socket")
        await client.close()
        await client.close()
        # A closed client is done, even one that never opened a connection.
        with pytest.raises(RPCClientUnusableError):
            await client.call.echo("hello")


async def test_client_call_after_close(socket_server_path):
    """A closed client is done, so it must not reconnect for a later call."""
    async with asyncio.timeout(5):
        async with SocketAsyncRPCClient(socket_server_path) as client:
            assert await client.call.echo("hello") == "socket: hello"
        with pytest.raises(RPCClientUnusableError):
            await client.call.echo("world")


@pytest_asyncio.fixture()
async def deaf_server_path(path_tmp):
    """The path of a server that accepts connections and then never reads a single byte."""
    writers = []
    path = path_tmp / "deaf_socket"
    server = await asyncio.start_unix_server(lambda reader, writer: writers.append(writer), path)
    try:
        yield path
    finally:
        for writer in writers:
            writer.transport.abort()
        server.close()
        await server.wait_closed()


async def _park_a_request_in_the_write_buffer(client: SocketAsyncRPCClient):
    """Leave the request of a cancelled call above the write high-water mark of the client.

    This is the state that a build shutting down leaves behind
    when the terminal user interface has stopped reading the reports sent to it.
    """
    await client._ensure_connected()
    transport = client._writer.transport
    high_water = transport.get_write_buffer_limits()[1]
    task = asyncio.create_task(client.call.echo(b"x" * (4 * 1024 * 1024)))
    while transport.get_write_buffer_size() <= high_water:
        await asyncio.sleep(0)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


async def test_client_close_bounds_its_own_send(deaf_server_path, monkeypatch: pytest.MonkeyPatch):
    """Closing must not wait for a server that has stopped reading.

    Both the close message and a graceful close of the connection wait for the write buffer,
    which such a server never empties,
    so without a limit of its own the close never returns.
    """
    monkeypatch.setattr("stepup.core.rpc.CLOSE_TIMEOUT", 0.1)
    async with asyncio.timeout(5):
        client = SocketAsyncRPCClient(deaf_server_path)
        await _park_a_request_in_the_write_buffer(client)
        close_task = asyncio.create_task(client.close())
        # Shielded: a close that waits for this server waits forever and ignores a cancellation,
        # so an unshielded wait would hang the test suite instead of failing this test.
        await asyncio.wait_for(asyncio.shield(close_task), 1.0)
        assert client._writer.is_closing()
        assert client._recv_task.done()


async def test_client_close_completes_when_it_is_cancelled(deaf_server_path):
    """A cancelled close must still end the connection instead of getting stuck.

    The connection is closed while the cancellation is already unwinding,
    where a second cancellation cannot break the close out of a wait anymore.
    """
    async with asyncio.timeout(5):
        client = SocketAsyncRPCClient(deaf_server_path)
        await _park_a_request_in_the_write_buffer(client)
        close_task = asyncio.create_task(client.close())
        # Cancel only once the close message has been handed to the write buffer,
        # which is where the close would otherwise wait for the server.
        counter = client._counter
        while client._counter == counter:
            await asyncio.sleep(0)
        close_task.cancel()
        # Shielded for the reason given in `test_client_close_bounds_its_own_send`.
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(asyncio.shield(close_task), 1.0)
        assert client._writer.is_closing()
        assert client._recv_task.done()


def test_sync_client_close_is_idempotent(socket_server_path):
    """An explicit close inside a `with` block must not send a second close message."""
    with SocketSyncRPCClient(socket_server_path) as client:
        assert client.call.echo("hello", _rpc_timeout=5) == "socket: hello"
        client.close()
        counter = client._counter
    assert client._counter == counter


def test_sync_client_close_closes_the_socket(socket_server_path):
    """A closed client must not leave its socket open."""
    with SocketSyncRPCClient(socket_server_path) as client:
        assert client.call.echo("hello", _rpc_timeout=5) == "socket: hello"
        sock = client._ensure_connected(5)
    assert sock.fileno() == -1


def test_sync_client_close_ignores_a_gone_server(socket_server_path):
    """A server that is already gone cannot be told that the conversation is over.

    The close message is a courtesy, so failing to send it must not raise,
    just like in `SocketAsyncRPCClient.close`.
    """
    with SocketSyncRPCClient(socket_server_path) as client:
        assert client.call.echo("hello", _rpc_timeout=5) == "socket: hello"
        # Make the close message fail, like a server that vanished in the meantime.
        client._socket.close()


def test_sync_client_close_without_connecting(path_tmp):
    """Closing a client that was never used must not connect just to say goodbye.

    There is no server at this path, so an attempt to connect fails loudly.
    """
    client = SocketSyncRPCClient(path_tmp / "never_created_socket")
    client.close()
    client.close()
    # A closed client is done, even one that never opened a connection.
    with pytest.raises(RPCClientUnusableError):
        client.call.echo("hello", _rpc_timeout=5)


class _RecordingSocket:
    """Socket stub that records what a client does to it, in order."""

    def __init__(self, sendall_error: BaseException | None = None):
        self.events = []
        self.sendall_error = sendall_error

    def settimeout(self, timeout: float | None):
        self.events.append(("settimeout", timeout))

    def connect(self, path):
        self.events.append(("connect", str(path)))

    def sendall(self, data: bytes):
        self.events.append(("sendall", len(data)))
        if self.sendall_error is not None:
            raise self.sendall_error

    def close(self):
        self.events.append(("close",))


class _FakeSocketModule:
    """Stand-in for the `socket` module, handing out one `_RecordingSocket`."""

    AF_UNIX = "AF_UNIX"

    def __init__(self, sock: _RecordingSocket):
        self._sock = sock

    def socket(self, family) -> _RecordingSocket:
        assert family == self.AF_UNIX
        return self._sock


def _recording_client(
    path_tmp: Path, monkeypatch: pytest.MonkeyPatch, sendall_error: BaseException | None = None
) -> tuple[SocketSyncRPCClient, _RecordingSocket]:
    """Build a synchronous client whose socket records the calls made on it."""
    sock = _RecordingSocket(sendall_error)
    monkeypatch.setattr("stepup.core.rpc.socket", _FakeSocketModule(sock))
    return SocketSyncRPCClient(path_tmp / "socket"), sock


def test_sync_client_times_out_the_connect(path_tmp, monkeypatch: pytest.MonkeyPatch):
    """The timeout of a call must be in force before the connect that the call triggers.

    A connect is the one socket operation of a call that happens before anything is sent,
    so a timeout applied afterwards would let it block for as long as the kernel allows.
    """
    client, sock = _recording_client(path_tmp, monkeypatch)
    assert client._ensure_connected(2.5) is sock
    assert sock.events == [("settimeout", 2.5), ("connect", str(client.socket_path))]
    # A later call reuses the connection and applies its own timeout.
    assert client._ensure_connected(0.5) is sock
    assert sock.events[2:] == [("settimeout", 0.5)]


def test_sync_client_close_bounds_its_own_send(path_tmp, monkeypatch: pytest.MonkeyPatch):
    """Closing must not inherit the unbounded timeout that `NO_RPC_TIMEOUT` leaves behind.

    Without a limit of its own, the close message of a client that last called with
    `NO_RPC_TIMEOUT` blocks forever on a server that has stopped reading.
    """
    client, sock = _recording_client(path_tmp, monkeypatch)
    client._ensure_connected(_resolve_socket_timeout(NO_RPC_TIMEOUT))
    assert sock.events == [("settimeout", None), ("connect", str(client.socket_path))]
    client.close()
    assert sock.events[2:] == [
        ("settimeout", CLOSE_TIMEOUT),
        ("sendall", HEADER_SIZE),
        ("close",),
    ]


def test_sync_client_close_ignores_a_timed_out_send(path_tmp, monkeypatch: pytest.MonkeyPatch):
    """A close message that cannot leave in time is no reason for `close` to raise."""
    client, sock = _recording_client(path_tmp, monkeypatch, TimeoutError("send buffer full"))
    client._ensure_connected(None)
    client.close()
    assert sock.events[-1] == ("close",)
    assert client._socket is None
    assert client._reader is None


@pytest.fixture
def clean_timeout_cache():
    """Isolate a test that sets `STEPUP_SYNC_RPC_TIMEOUT` from the default cached elsewhere."""
    _default_sync_rpc_timeout.cache_clear()
    yield
    _default_sync_rpc_timeout.cache_clear()


def test_resolve_socket_timeout_reads_the_environment_once(
    clean_timeout_cache, monkeypatch: pytest.MonkeyPatch
):
    """Every call that relies on the default must wait equally long.

    An environment that changes while the process runs would otherwise
    give the calls of one run different timeouts.
    """
    monkeypatch.setenv("STEPUP_SYNC_RPC_TIMEOUT", "12.5")
    assert _resolve_socket_timeout(None) == 12.5
    monkeypatch.setenv("STEPUP_SYNC_RPC_TIMEOUT", "99.0")
    assert _resolve_socket_timeout(None) == 12.5
    # A call with a timeout of its own never consults the environment.
    assert _resolve_socket_timeout(2.5) == 2.5
    assert _resolve_socket_timeout(NO_RPC_TIMEOUT) is None


def test_resolve_socket_timeout_rejects_a_malformed_environment_variable(
    clean_timeout_cache, monkeypatch: pytest.MonkeyPatch
):
    """A misconfigured timeout keeps raising, instead of only on the first call that needs it."""
    monkeypatch.setenv("STEPUP_SYNC_RPC_TIMEOUT", "soon")
    for _ in range(2):
        with pytest.raises(ConfigError, match="must hold a number"):
            _resolve_socket_timeout(None)


def test_sync_client_call_after_close(socket_server_path):
    """A closed client is done, so it must not reconnect for a later call."""
    with SocketSyncRPCClient(socket_server_path) as client:
        assert client.call.echo("hello", _rpc_timeout=5) == "socket: hello"
    with pytest.raises(RPCClientUnusableError):
        client.call.echo("world", _rpc_timeout=5)


@pytest.mark.parametrize(
    ("name", "args", "kwargs", "result"),
    [
        ("foo", ["gg", 1], {}, "foo('gg', 1)"),
        ("bar", [], {"a": 1, "b": [3, 4, "qq"]}, "bar(a=1, b=[3, 4, 'qq'])"),
        ("none", [], {}, "none()"),
        ("mixed", [()], {"_q": 5}, "mixed((), _q=5)"),
    ],
)
def test_rpc_call_str(name, args, kwargs, result):
    assert str(RPCCall(name, args, kwargs)) == result


def test_rpc_call_str_without_arguments():
    assert str(RPCCall("foo")) == "foo()"


def test_rpc_call_normalizes_its_arguments():
    """A call built from a list of arguments is the one built from the same tuple."""
    assert RPCCall("foo", ["gg", 1]) == RPCCall("foo", ("gg", 1))


async def test_socket_not_allowed(sc):
    with pytest.raises(RPCError):
        await sc.call.not_allowed()


async def test_socket_not_defined(sc):
    with pytest.raises(RPCError):
        await sc.call.not_defined()


def test_call_interface_refuses_dunder_names():
    """Anything probing for a special method must not receive a remote call.

    Without the guard, `copy.deepcopy` finds a `__deepcopy__` that sends an RPC call
    and returns whatever came back, which is `None` for the dummy client used here.
    """
    client = DummySyncRPCClient()
    with pytest.raises(AttributeError):
        _ = client.call.__deepcopy__
    assert isinstance(copy.deepcopy(client.call), RemoteCallProxy)


async def test_call_interface_passes_a_name_keyword(sc):
    """A remote procedure may have a parameter called `name`, like the client's `__call__`.

    `RemoteCallProxy` binds the procedure name positionally,
    so without a positional-only `name` in `__call__`
    the two would collide into `got multiple values for argument 'name'`.
    """
    assert await sc.call.greet(name="world") == "socket: hello world"
    assert await sc.call.greet("world") == "socket: hello world"


def test_sync_call_interface_passes_a_name_keyword(socket_server_path):
    """Same collision as above, on the synchronous client."""
    with SocketSyncRPCClient(socket_server_path) as client:
        assert client.call.greet(name="world", _rpc_timeout=5) == "socket: hello world"


def test_dummy_call_interface_passes_a_name_keyword():
    """The dummy clients follow the same signature, so a call reaches them the same way."""
    DummySyncRPCClient().call.greet(name="world")


def test_is_rpc_allowed():
    """The marker set by `allow_rpc` is only meaningful through `is_rpc_allowed`."""
    handler = EchoHandler("x")
    assert is_rpc_allowed(handler.echo)
    assert not is_rpc_allowed(handler.not_allowed)


#
# The wire format.
#


@pytest.mark.parametrize(
    ("call_id", "body", "data"),
    [
        (1, b"body", b"\x00" * 7 + b"\x01" + b"\x00" * 7 + b"\x04" + b"body"),
        (258, None, b"\x00" * 6 + b"\x01\x02" + b"\x00" * 8),
        (1, b"", b"\x00" * 7 + b"\x01" + b"\x00" * 8),
    ],
)
def test_encode_message(call_id: int, body: bytes | None, data: bytes):
    """The two header fields are big-endian and fixed-width, and an empty body carries no bytes."""
    assert _encode_message(call_id, body) == data


class _BytesReader:
    """Reader stub that serves a fixed byte string, like a peer that sent one message."""

    def __init__(self, data: bytes):
        self._data = data
        self._pos = 0

    async def readexactly(self, size: int) -> bytes:
        if self._pos + size > len(self._data):
            raise asyncio.IncompleteReadError(partial=b"", expected=size)
        chunk = self._data[self._pos : self._pos + size]
        self._pos += size
        return chunk


@pytest.mark.parametrize("body", [b"body", None])
async def test_stream_message_round_trip(body: bytes | None):
    """What one side frames, the other side must read back."""
    async with asyncio.timeout(5):
        assert await _recv_stream_message(_BytesReader(_encode_message(42, body))) == (42, body)


def test_decode_header_rejects_an_impossible_size():
    """Bytes that are not a header announce a size that no RPC body could ever have."""
    with pytest.raises(RPCError, match="exceeds the maximum"):
        _decode_header((1).to_bytes(FIELD_SIZE, "big") + b"HTTP/1.1")


async def test_recv_stream_message_rejects_an_impossible_size():
    """A corrupt size must not be mistaken for a clean disconnect, nor be waited for."""
    header = (1).to_bytes(FIELD_SIZE, "big") + (MAX_BODY_SIZE + 1).to_bytes(FIELD_SIZE, "big")
    async with asyncio.timeout(5):
        with pytest.raises(RPCError, match="exceeds the maximum"):
            await _recv_stream_message(_BytesReader(header))


def test_decode_request_rejects_a_body_that_is_not_a_request():
    """A peer speaking another protocol must be reported as such, not as an unpickling failure."""
    with pytest.raises(RPCError, match="Could not read the body"):
        _decode_request(b"GET / HTTP/1.1")


def test_decode_response_rejects_a_body_that_is_not_a_response():
    """The same holds for the other direction, where the error also names the call."""
    with pytest.raises(RPCError, match=r"response to echo\('hello'\)"):
        _decode_response(
            b"HTTP/1.1 200 OK", RPCCall("echo", ["hello"]), server_log_description=None
        )


def test_decode_response_reports_a_missing_reply():
    """An empty body means that no reply is coming for this call."""
    with pytest.raises(RPCError, match="could not send a reply"):
        _decode_response(None, RPCCall("echo", ["hello"]), server_log_description=None)


def test_decode_response_falls_back_to_a_generic_log_description():
    """Without a description, the message can only point at the server process."""
    with pytest.raises(RPCError, match="recorded in the log of the RPC server process"):
        _decode_response(None, RPCCall("echo", ["hello"]), server_log_description=None)


def test_decode_response_names_the_log_of_the_server_that_was_called():
    """With a description, the message sends the user to that log and to no other."""
    with pytest.raises(RPCError, match=r"recorded in `some/where\.log`"):
        _decode_response(
            None, RPCCall("echo", ["hello"]), server_log_description="`some/where.log`"
        )


class _FakeSocket:
    """Socket stub that serves a fixed byte string and then reports EOF."""

    def __init__(self, data: bytes = b""):
        self._data = data

    def recv(self, size: int) -> bytes:
        chunk = self._data[:size]
        self._data = self._data[size:]
        return chunk


def _client_with_socket(path_tmp: Path, data: bytes) -> SocketSyncRPCClient:
    """Build a synchronous client that reads `data` from a fake socket."""
    client = SocketSyncRPCClient(path_tmp / "socket")
    sock = _FakeSocket(data)
    client._socket = sock
    client._reader = _SocketReader(sock, client.socket_path)
    return client


def test_sync_client_rejects_a_response_for_another_call(path_tmp):
    """A response that answers another call means the conversation is out of step."""
    client = _client_with_socket(path_tmp, _encode_message(3, b"body"))
    with pytest.raises(RPCError, match="call id 3"):
        client._recv_response(2)


def test_sync_client_reports_a_truncated_response(path_tmp):
    """A connection that ends mid-message must say what was still expected."""
    client = _client_with_socket(path_tmp, _encode_message(1, b"body")[:-2])
    with pytest.raises(ConnectionResetError, match="2 more bytes"):
        client._recv_response(1)


#
# Errors raised by the server while handling a call.
#


class _TwoArgUsageError(UsageError):
    """A `UsageError` that cannot be rebuilt from a message alone.

    No such subclass exists in StepUp today, so this is the stand-in for a future one.
    """

    def __init__(self, message: str, detail: str):
        super().__init__(f"{message}: {detail}")


def _remote_failure(exc: BaseException) -> RemoteFailure:
    """Build the payload the server would send for *exc*, with a real traceback."""
    try:
        raise exc
    except type(exc):
        return RemoteFailure.from_exception(exc)


def test_remote_failure_from_exception():
    err = _remote_failure(CyclicError("cyclic"))
    assert err.module == "stepup.core.exceptions"
    assert err.qualname == "CyclicError"
    assert err.message == "cyclic"
    assert err.traceback_text.startswith("Traceback (most recent call last):\n")
    assert err.traceback_text.endswith("stepup.core.exceptions.CyclicError: cyclic\n")
    assert err.usage


def test_remote_failure_from_internal_exception():
    """The classification happens on the server, where the exception class is importable."""
    assert not _remote_failure(RuntimeError("bug")).usage


def test_remote_failure_is_picklable():
    """The reply travels through `pickle`, which the payload must survive by construction."""
    err = _remote_failure(CyclicError("cyclic"))
    assert pickle.loads(pickle.dumps(err)) == err


def test_to_exception_returns_the_original_class():
    exc = _remote_failure(CyclicError("cyclic")).to_exception()
    assert type(exc) is CyclicError
    assert str(exc) == "cyclic"


@pytest.mark.parametrize(
    ("module", "qualname"),
    [
        # The class is not importable in the client process.
        ("no_such_module", "CyclicError"),
        ("stepup.core.exceptions", "NoSuchError"),
        # The name exists but is not a class.
        ("stepup.core.exceptions", "__doc__"),
        # The class exists but is not one this design vouches for.
        ("builtins", "ValueError"),
    ],
)
def test_to_exception_falls_back_to_rpc_error(module: str, qualname: str):
    err = RemoteFailure(module, qualname, "cyclic", "traceback", True)
    exc = err.to_exception()
    assert type(exc) is RPCError
    assert str(exc) == "cyclic"


def test_to_exception_falls_back_for_a_richer_constructor():
    """A subclass that needs more than a message cannot be rebuilt from one."""
    err = _remote_failure(_TwoArgUsageError("cyclic", "detail"))
    assert err.qualname == "_TwoArgUsageError"
    exc = err.to_exception()
    assert type(exc) is RPCError
    assert str(exc) == "cyclic: detail"


def test_raise_remote_error_raises_the_usage_error_class():
    """A usage error reaches the caller as itself, so `except GraphError:` works as expected."""
    err = _remote_failure(CyclicError("cyclic"))
    with pytest.raises(CyclicError, match=r"^cyclic$") as exc_info:
        _raise_remote_error(err, RPCCall("define_step"))
    # `from None`: a chained `RPCError` would put StepUp's plumbing back in the traceback.
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None


def test_raise_remote_error_wraps_a_usage_error_with_stepup_debug(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("STEPUP_DEBUG", "1")
    err = _remote_failure(CyclicError("cyclic"))
    with pytest.raises(RPCError) as exc_info:
        _raise_remote_error(err, RPCCall("define_step", ["cp a b"]))
    assert str(RPCCall("define_step", ["cp a b"])) in str(exc_info.value)
    assert err.traceback_text in str(exc_info.value)


def test_raise_remote_error_wraps_an_internal_error():
    """A bug in the server keeps the full traceback, which is what a bug report needs."""
    err = _remote_failure(RuntimeError("bug"))
    with pytest.raises(RPCError) as exc_info:
        _raise_remote_error(err, RPCCall("define_step"))
    assert err.traceback_text in str(exc_info.value)


async def test_handle_call_logs_a_harmless_record(caplog: pytest.LogCaptureFixture):
    """The traceback the client will hide is kept in the director log, without raising a flag.

    `stepup build` scans its own log and fails the build over any `DIRECTOR_LOG_CHECKS` match,
    so this record must be neither an `ERROR` nor a line that reads like dangling work.
    """
    with caplog.at_level(logging.INFO, logger="stepup.core.rpc"):
        err = await _call_and_capture_failure(EchoHandler("caplog"), RPCCall("raise_usage"))
    assert isinstance(err, RemoteFailure)
    assert err.qualname == "CyclicError"
    (record,) = [record for record in caplog.records if record.name == "stepup.core.rpc"]
    assert record.levelname not in ("ERROR", "CRITICAL")
    assert err.traceback_text in record.getMessage()
    message = record.getMessage()
    assert [label for pattern, label in DIRECTOR_LOG_CHECKS if pattern.search(message)] == []


async def test_socket_usage_error(sc):
    """A usage error survives the wire as the class the server raised."""
    with pytest.raises(CyclicError, match=r"^cyclic$"):
        await sc.call.raise_usage()


async def test_socket_internal_error(sc):
    with pytest.raises(RPCError):
        await sc.call.raise_internal()


def test_sync_socket_usage_error(socket_server_path):
    """The synchronous client is the one a `plan.py` uses, so it must behave the same."""
    with SocketSyncRPCClient(socket_server_path) as client:
        with pytest.raises(CyclicError, match=r"^cyclic$"):
            client.call.raise_usage(_rpc_timeout=5)
        with pytest.raises(RPCError):
            client.call.raise_internal(_rpc_timeout=5)


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


async def test_serve_connection_survives_reset_during_close():
    """A connection reset can surface on `wait_closed()`, not just `drain()`.

    Both must be tolerated, or an unhandled `ConnectionResetError` propagates out of the
    task spawned for this connection (visible as "Unhandled exception in
    client_connected_cb" in `SocketRPCServer.serve`).
    """
    async with asyncio.timeout(5):
        writer = _ResetOnCloseWriter()
        server = SocketRPCServer(EchoHandler("x"), "unused-socket")
        await server._serve_connection(_ImmediateEOFReader(), writer)
        assert writer.closed


class _RecordingWriter:
    """Writer stub whose send and close operations always succeed, keeping what was written."""

    def __init__(self):
        self.data = b""
        self.closed = False

    def write(self, data):
        self.data += data

    async def drain(self):
        pass

    def close(self):
        self.closed = True

    async def wait_closed(self):
        pass


class _ResetReader:
    """Reader stub whose read raises a raw `ConnectionResetError`, like a peer that reset
    the connection instead of closing it cleanly (no `IncompleteReadError` involved).
    """

    async def readexactly(self, n: int):
        raise ConnectionResetError("Connection reset by peer")


async def test_recv_stream_message_treats_reset_like_eof():
    """A reset connection must be treated the same as a clean EOF.

    `asyncio.StreamReader.readexactly()` raises `IncompleteReadError` on a clean EOF, but a
    genuine reset while a read is pending surfaces as a raw `ConnectionResetError` instead
    (the stream's stored transport exception is raised directly).
    Both mean that the peer is gone, so both must be reported as such
    instead of escaping into the RPC loop.
    """
    async with asyncio.timeout(5):
        assert await _recv_stream_message(_ResetReader()) is None


async def test_serve_connection_survives_reset_during_recv():
    """Same failure mode as above, exercised through `SocketRPCServer._serve_connection`."""
    async with asyncio.timeout(5):
        server = SocketRPCServer(EchoHandler("x"), "unused-socket")
        await server._serve_connection(_ResetReader(), _RecordingWriter())


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


def _send_loop_connection(writer) -> RPCServerConnection:
    """Build a connection whose send loop can be run on its own, without a client.

    The reader is never touched by the send loop, so it only has to exist.
    """
    return RPCServerConnection(EchoHandler("x"), _ImmediateEOFReader(), writer)


async def test_send_loop_stops_gracefully_on_connection_reset():
    """When the client is already gone, the send loop must not crash the connection task.

    A reply that cannot be sent to a peer that disappeared is no reason to raise:
    the loop stops the connection instead, so that no `ConnectionResetError` escapes
    (which would show up as "Unhandled exception in client_connected_cb").
    """
    async with asyncio.timeout(5):
        connection = _send_loop_connection(_AlwaysResetWriter())
        await connection._completed.put((1, asyncio.ensure_future(_return("ok"))))
        await connection._send_loop()
        assert connection._stop_event.is_set()


async def test_send_loop_does_not_mask_original_error_with_connection_error():
    """A genuine handler-side failure must surface as itself, not as a `ConnectionError`.

    If the connection also happens to be dead, the loop's own attempt to notify the
    (already gone) client of the failure must not replace the original exception with the
    `ConnectionError` from that doomed notification attempt.
    """
    async with asyncio.timeout(5):
        connection = _send_loop_connection(_AlwaysResetWriter())
        await connection._completed.put((1, asyncio.ensure_future(_boom())))
        with pytest.raises(_MarkerError):
            await connection._send_loop()


class _Unpicklable:
    """A value that cannot travel to the client, like a handler returning an open file."""

    def __reduce__(self):
        raise TypeError("cannot pickle this")


async def test_send_loop_closes_its_iterator_when_the_peer_is_gone():
    """Regression test: the send loop returns early, so it must close the iterator it consumes.

    Leaving that to the finalization of the abandoned iterator is what `iter_until_stopped`
    warns about: it may never happen when the event loop is already shutting down,
    and the tasks it was waiting on then show up in the director log as a bug in StepUp.
    The check therefore does not let the event loop settle first:
    the iterator must be closed by the time the send loop returns.
    """
    async with asyncio.timeout(5):
        connection = _send_loop_connection(_AlwaysResetWriter())
        await connection._completed.put((1, asyncio.ensure_future(_return("ok"))))
        await connection._send_loop()
        current = asyncio.current_task()
        uncancelled = [
            task.get_name()
            for task in asyncio.all_tasks()
            if task is not current and not task.cancelling()
        ]
        assert uncancelled == []


async def test_send_loop_drops_the_reply_of_a_cancelled_call():
    """A cancelled call has no reply to send, and must not end the send loop.

    The receive loop cancels the calls in flight when it tears the connection down,
    after which the done callback hands them to the send loop like any other completed task.
    Awaiting such a task raises a `CancelledError`,
    which would stop the send loop without anything reporting it.
    """
    async with asyncio.timeout(5):
        connection = _send_loop_connection(_RecordingWriter())
        cancelled = asyncio.ensure_future(_return("dropped"))
        cancelled.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await cancelled
        await connection._completed.put((1, cancelled))
        await connection._completed.put((2, asyncio.ensure_future(_return("ok"))))
        send_task = asyncio.create_task(connection._send_loop(), name="send-loop")
        while connection.writer.data == b"" and not send_task.done():
            await asyncio.sleep(0)
        connection.stop()
        await send_task
        assert connection.writer.data == _encode_message(2, _encode_body("ok"))


class _UnpicklableHandler:
    """Handler whose reply cannot be sent, like one returning an object holding an open file."""

    @allow_rpc
    def unpicklable(self):
        return _Unpicklable()


class _OneRequestReader:
    """Reader stub that yields a single request and then blocks, or reports EOF when `eof`.

    Blocking is what an idle connection looks like:
    the receive loop waits for the next request until something stops it.
    Reporting EOF is a peer that is gone,
    e.g. a step process killed while its call was in flight.
    """

    def __init__(self, name: str, eof: bool = False):
        request = pickle.dumps(RPCCall(name))
        self._data = (1).to_bytes(8) + len(request).to_bytes(8) + request
        self._pos = 0
        self._eof = eof

    async def readexactly(self, size: int) -> bytes:
        if self._pos >= len(self._data):
            if self._eof:
                raise asyncio.IncompleteReadError(partial=b"", expected=size)
            await asyncio.Event().wait()
        chunk = self._data[self._pos : self._pos + size]
        self._pos += size
        return chunk


async def test_serve_stops_the_receive_loop_when_the_send_loop_fails():
    """A failure in one loop must not leave the other one serving a doomed connection.

    The receive loop is waiting for the next request, which an idle client will not send,
    so nothing but the failure of the send loop can end it.
    """
    async with asyncio.timeout(5):
        with pytest.raises(ExceptionGroup) as exc_info:
            await RPCServerConnection(
                _UnpicklableHandler(), _OneRequestReader("unpicklable"), _RecordingWriter()
            ).serve()
        assert [type(exc) for exc in exc_info.value.exceptions] == [TypeError]
        assert await settled_task_names() == []


class _BlockingHandler:
    """Handler whose call only finishes when the test releases it, to keep it in flight.

    A handler that spans more than one await is why the receive loop waits for it:
    a declaration that arrived complete on the wire may be applied in more than one
    transaction, so a handler that is cancelled halfway applies only part of it.
    """

    def __init__(self):
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.finished = False

    @allow_rpc
    async def block(self):
        self.started.set()
        await self.release.wait()
        self.finished = True


async def test_serve_completes_a_call_in_flight_when_the_peer_is_gone():
    """A request that arrived complete on the wire is handled completely.

    The peer disappears while the handler is running, which is what a step process killed
    during an `amend_step` call looks like. The connection is torn down either way,
    but its handler may not be left running without an owner.
    """
    async with asyncio.timeout(5):
        handler = _BlockingHandler()
        connection = RPCServerConnection(
            handler, _OneRequestReader("block", eof=True), _RecordingWriter()
        )
        serve_task = asyncio.create_task(connection.serve(), name="serve")
        await handler.started.wait()
        assert "RPC:block-1" in await settled_task_names()
        assert not serve_task.done()
        handler.release.set()
        await serve_task
        assert handler.finished
        assert await settled_task_names() == []


async def test_stop_completes_a_call_in_flight():
    """A server that stops a connection lets the handler of a call in flight finish.

    The reply is dropped, because the send loop returns as soon as the connection is stopped,
    but the mutation that the request describes is applied completely.
    """
    async with asyncio.timeout(5):
        handler = _BlockingHandler()
        connection = RPCServerConnection(handler, _OneRequestReader("block"), _RecordingWriter())
        serve_task = asyncio.create_task(connection.serve(), name="serve")
        await handler.started.wait()
        connection.stop()
        assert "RPC:block-1" in await settled_task_names()
        assert not serve_task.done()
        handler.release.set()
        await serve_task
        assert handler.finished
        assert connection.writer.data == b""


async def test_serve_cancels_a_call_in_flight_when_it_is_cancelled():
    """Waiting for the handlers must not outlive the teardown of the server itself.

    The cancellation of `serve` reaches the handlers through the gather in the receive loop,
    so none of them is left pending when the event loop is closed.
    """
    async with asyncio.timeout(5):
        handler = _BlockingHandler()
        connection = RPCServerConnection(
            handler, _OneRequestReader("block", eof=True), _RecordingWriter()
        )
        serve_task = asyncio.create_task(connection.serve(), name="serve")
        await handler.started.wait()
        serve_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await serve_task
        assert not handler.finished
        assert await settled_task_names() == []


async def test_send_loop_answers_an_unpicklable_result_with_an_empty_body():
    """A reply that cannot be pickled is answered with the empty-body sentinel.

    The client cannot be told why, only that no reply is coming for this call,
    so the sentinel must reach it before the original exception tears the connection down.
    """
    async with asyncio.timeout(5):
        connection = _send_loop_connection(_RecordingWriter())
        await connection._completed.put((7, asyncio.ensure_future(_return(_Unpicklable()))))
        with pytest.raises(TypeError):
            await connection._send_loop()
        assert connection.writer.data == (7).to_bytes(8) + (0).to_bytes(8)


@pytest_asyncio.fixture()
async def no_reply_server_path(path_tmp):
    """Path of a socket server that answers every call with an empty body.

    This is what a server sends when it cannot send the reply itself,
    e.g. because the result of the call cannot be pickled.
    """
    path = path_tmp / "no_reply_socket"

    async def handle(reader, writer):
        with contextlib.suppress(asyncio.IncompleteReadError, ConnectionError):
            while True:
                call_id = int.from_bytes(await reader.readexactly(8))
                size = int.from_bytes(await reader.readexactly(8))
                if size == 0:
                    # The client is closing the connection.
                    break
                await reader.readexactly(size)
                writer.write(call_id.to_bytes(8) + (0).to_bytes(8))
                await writer.drain()
        writer.close()

    server = await asyncio.start_unix_server(handle, path)
    try:
        yield path
    finally:
        server.close()
        await server.wait_closed()


async def test_client_call_raises_when_server_cannot_reply(no_reply_server_path):
    """An empty response body must reach the caller as an `RPCError`.

    The same sentinel means "the client is closing" in a request and "no reply is coming"
    in a response, so a client that treats it as a body ends up unpickling `None`.
    """
    async with asyncio.timeout(5):
        async with SocketAsyncRPCClient(no_reply_server_path) as client:
            with pytest.raises(RPCError, match="could not send a reply"):
                await client.call.echo("hello")


async def test_sync_client_call_raises_when_server_cannot_reply(no_reply_server_path):
    """The synchronous client is the one a `plan.py` uses, so it must behave the same.

    The call runs in a worker thread, because the server it talks to is served by
    the event loop of this test, which a blocking call would otherwise starve.
    """

    def call_echo():
        with SocketSyncRPCClient(no_reply_server_path) as client:
            client.call.echo("hello", _rpc_timeout=5)

    async with asyncio.timeout(5):
        with pytest.raises(RPCError, match="could not send a reply"):
            await asyncio.to_thread(call_echo)


async def test_client_no_reply_error_names_the_log_of_its_own_server(no_reply_server_path):
    """A client carries the description of its server's log into the error of a failed call."""
    async with asyncio.timeout(5):
        async with SocketAsyncRPCClient(
            no_reply_server_path, server_log_description="`somewhere.log`"
        ) as client:
            with pytest.raises(RPCError, match=r"recorded in `somewhere\.log`"):
                await client.call.echo("hello")


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

    Only the receive loop can complete the future of a call, so a peer that goes away
    without answering leaves the caller waiting for a response that can never arrive.
    """
    async with asyncio.timeout(5):
        client = SocketAsyncRPCClient(vanishing_server_path)
        with pytest.raises(ConnectionResetError) as exc_info:
            await client.call.echo("hello")
        # A peer that went away is a genuine reset, not a client that made itself unusable.
        assert not isinstance(exc_info.value, RPCClientUnusableError)
        await client.close()


async def test_client_close_after_peer_gone(vanishing_server_path):
    """Closing a client whose peer is already gone must complete without hanging or raising."""
    async with asyncio.timeout(5):
        client = SocketAsyncRPCClient(vanishing_server_path)
        with pytest.raises(ConnectionResetError):
            await client.call.echo("hello")
        await client.close()
        # A new call cannot be answered either, so it must fail instead of waiting forever.
        with pytest.raises(RPCClientUnusableError):
            await client.call.echo("world")


async def test_client_call_cancelled_leaves_the_client_usable(sc):
    """A caller that goes away must not break the receive loop for the others.

    The response to the cancelled call still arrives,
    for a caller that is no longer waiting for it.
    """
    async with asyncio.timeout(5):
        task = asyncio.create_task(sc.call.echo("slow", 0.2), name="cancelled-call")
        await asyncio.sleep(0.05)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        await asyncio.sleep(0.25)
        assert await sc.call.echo("hello") == "socket: hello"


async def test_client_call_that_cannot_be_sent_leaves_no_pending_call(sc):
    """A request that never left the client must not leave a call waiting for a response.

    Nobody awaits the future of a call that failed to be sent,
    so the receive loop would later fail a future that nothing retrieves,
    which asyncio reports as `Future exception was never retrieved`.
    `stepup build` scans its own log for exactly that message.
    """
    async with asyncio.timeout(5):
        assert await sc.call.echo("hello") == "socket: hello"
        writer = sc._writer
        sc._writer = _AlwaysResetWriter()
        try:
            with pytest.raises(ConnectionResetError):
                await sc.call.echo("world")
            assert sc._pending == {}
        finally:
            sc._writer = writer


@pytest_asyncio.fixture()
async def corrupt_server_path(path_tmp):
    """Path of a socket server whose response is not an RPC message.

    Its header announces a body that no response could ever have,
    like a peer speaking another protocol.
    """
    path = path_tmp / "corrupt_socket"

    async def handle(reader, writer):
        with contextlib.suppress(asyncio.IncompleteReadError, ConnectionError):
            call_id, size = _decode_header(await reader.readexactly(HEADER_SIZE))
            await reader.readexactly(size)
            writer.write(
                call_id.to_bytes(FIELD_SIZE, "big")
                + (MAX_BODY_SIZE + 1).to_bytes(FIELD_SIZE, "big")
            )
            await writer.drain()
        writer.close()

    server = await asyncio.start_unix_server(handle, path)
    try:
        yield path
    finally:
        server.close()
        await server.wait_closed()


async def test_client_call_fails_when_the_receive_loop_fails(corrupt_server_path):
    """A call in flight must not wait for a response that a failed receive loop cannot deliver.

    The failure of the loop is what explains the situation,
    so it surfaces at the next call and when closing the client,
    where a lost connection would leave the reason unsaid.
    """
    async with asyncio.timeout(5):
        client = SocketAsyncRPCClient(corrupt_server_path)
        with pytest.raises(ConnectionResetError):
            await client.call.echo("hello")
        with pytest.raises(RPCError, match="exceeds the maximum"):
            await client.call.echo("world")
        with pytest.raises(RPCError, match="exceeds the maximum"):
            await client.close()
