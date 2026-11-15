# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""The wire format of RPC messages, shared by the servers and the clients."""

import asyncio
import contextlib
import errno
import importlib
import logging
import pickle
import socket
import traceback
from collections.abc import AsyncIterator, Iterator
from functools import partial
from typing import Any

import attrs

from ..exceptions import RPCError, UsageError
from ..platform.asyncio import iter_until_stopped

__all__ = (
    "RPCCall",
    "RemoteFailure",
    "SocketReader",
    "encode_body",
    "is_peer_gone",
    "iter_stream_messages",
    "recv_socket_message",
    "send_socket_message",
    "send_stream_message",
    "suppress_peer_gone",
)

logger = logging.getLogger(__name__)


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


def is_peer_gone(exc: BaseException) -> bool:
    """Return whether an exception only means that the peer has closed the connection.

    Python raises a subclass of `ConnectionError` for most errors of this kind,
    such as `EPIPE` and `ECONNRESET`, but a plain `OSError` for `ENOTCONN`.
    The latter is what macOS reports when writing to a Unix domain socket that the peer closed,
    in the situation where Linux reports `EPIPE`.
    (Observed with Python 3.14 on a GitHub-hosted macOS runner.)
    On Windows, a TCP connection that the peer closed or reset raises
    `ConnectionResetError` (`WSAECONNRESET`) or `ConnectionAbortedError` (`WSAECONNABORTED`),
    and `errno.ENOTCONN` is `WSAENOTCONN`,
    so the same test covers that platform.
    (Observed with Python 3.14 on Windows 11.)
    """
    return isinstance(exc, ConnectionError) or (
        isinstance(exc, OSError) and exc.errno == errno.ENOTCONN
    )


@contextlib.contextmanager
def suppress_peer_gone() -> Iterator[None]:
    """Ignore an exception that only means that the peer has closed the connection.

    This is `contextlib.suppress(ConnectionError)`, extended as described in `is_peer_gone`.
    """
    try:
        yield
    except OSError as exc:
        if not is_peer_gone(exc):
            raise


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
    except asyncio.IncompleteReadError:
        # A graceful EOF: the peer closed the connection cleanly.
        return None
    except OSError as exc:
        # A reset while a read is pending surfaces as an `OSError` instead.
        # It means the same thing here: the peer is gone, so the RPC loop should stop.
        if not is_peer_gone(exc):
            raise
        return None
    return call_id, body


async def send_stream_message(writer: asyncio.StreamWriter, call_id: int, body: bytes | None):
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


async def iter_stream_messages(
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
class SocketReader:
    """Buffered reader over a blocking socket, the counterpart of `asyncio.StreamReader`."""

    sock: socket.socket = attrs.field()
    """The socket to read from."""

    socket_path: str = attrs.field()
    """The socket path of the server, which the error messages refer to."""

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


def recv_socket_message(reader: SocketReader) -> tuple[int, bytes | None]:
    """Read a single RPC message from a blocking socket.

    Parameters
    ----------
    reader
        The `SocketReader` to read the next message from.

    Returns
    -------
    call_id, body
        The call id and the body of the message, see the wire format above.

    Raises
    ------
    ConnectionResetError
        When the peer is gone, see `SocketReader.readexactly`.
        A blocking caller is waiting for one specific message,
        so a peer that is gone is an error here,
        while `_recv_stream_message` reports it as the end of the messages.
    RPCError
        When the header is not the header of an RPC message, see `_decode_header`.
    """
    call_id, size = _decode_header(reader.readexactly(HEADER_SIZE))
    return call_id, None if size == 0 else reader.readexactly(size)


def send_socket_message(sock: socket.socket, call_id: int, body: bytes | None):
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
# Both directions encode the same way, see `encode_body`,
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


def encode_body(payload: Any) -> bytes:
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
