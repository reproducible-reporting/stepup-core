# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Select the transport of the RPC servers and find a server by its socket path."""

import contextlib
import json
import logging
import os
import socket
import stat
import sys
import tempfile

import attrs

from ..exceptions import ConfigError, RPCError
from ..path import Path, StrPath
from ..platform.oscompat import af_unix_available

__all__ = (
    "RPC_TRANSPORTS",
    "TCPEndpoint",
    "read_endpoint",
    "select_rpc_transport",
    "set_nodelay",
)

logger = logging.getLogger(__name__)


#
# Endpoints
#
# A server listens on one of two transports, see `select_rpc_transport`.
# Its clients know it by a path, called the socket path, whatever the transport:
#
# - On a Unix domain socket, the socket path is the socket itself.
# - On a TCP socket, the socket path is an endpoint file,
#   which holds the address of the server and the token that a client sends before anything else.
#
# Either way, the server creates the socket path only once it listens,
# so that a path that exists means a server that accepts connections,
# see `SocketRPCServer.listen`.
#
# A client tells the two apart by the type of file at the socket path,
# so that only the server needs to know which transport is in use.
#


RPC_TRANSPORTS = ("unix", "tcp")
"""The transports that a server can listen on, the preferred one first."""


def select_rpc_transport() -> str:
    """Select the transport of the RPC servers of a build.

    A Unix domain socket is used by default, where the platform has them,
    and `STEPUP_RPC_TRANSPORT=tcp` selects a TCP socket on the loopback interface instead.
    On other platforms, `tcp` is the only choice.

    Returns
    -------
    transport
        One of `RPC_TRANSPORTS`.

    Raises
    ------
    ConfigError
        If `STEPUP_RPC_TRANSPORT` names a transport that cannot be used on this platform.
    """
    choices = RPC_TRANSPORTS if af_unix_available() else ("tcp",)
    transport = os.environ.get("STEPUP_RPC_TRANSPORT", "") or choices[0]
    if transport not in choices:
        raise ConfigError(
            f"STEPUP_RPC_TRANSPORT must be {' or '.join(choices)} on {sys.platform}, "
            f"got {transport!r}."
        )
    return transport


@attrs.define(frozen=True)
class TCPEndpoint:
    """How to connect to a server on the TCP transport, as written in its endpoint file."""

    host: str = attrs.field()
    """The address at which the server listens."""

    port: int = attrs.field()
    """The port at which the server listens."""

    token: bytes = attrs.field(repr=False)
    """The token that a client sends before its first request."""

    def write(self, path: StrPath):
        """Write the endpoint file, such that no client can ever read part of it.

        The file is written under a temporary name in the same directory,
        and then renamed to `path`, which makes it appear in a single step.
        On POSIX, only its owner can read it,
        even when the directory is accessible to others by mistake.
        On Windows, it inherits the access rules of the directory.

        Parameters
        ----------
        path
            The socket path of the server, which must not exist yet.
        """
        path = Path(path)
        fd, path_tmp = tempfile.mkstemp(dir=path.parent, prefix=f"{path.name}.", suffix=".tmp")
        path_tmp = Path(path_tmp)
        try:
            with open(fd, "w", encoding="utf-8", newline="\n") as fh:
                json.dump({"host": self.host, "port": self.port, "token": self.token.hex()}, fh)
            os.replace(path_tmp, path)
        except BaseException:
            path_tmp.remove_p()
            raise

    @classmethod
    def read(cls, path: StrPath) -> "TCPEndpoint":
        """Read the endpoint file of a server.

        Parameters
        ----------
        path
            The socket path of the server.

        Returns
        -------
        endpoint
            Where to connect and which token to send.

        Raises
        ------
        OSError
            When the file cannot be read.
        RPCError
            When the file is not an endpoint file.
        """
        text = Path(path).read_text(encoding="utf-8")
        try:
            data = json.loads(text)
            return cls(data["host"], int(data["port"]), bytes.fromhex(data["token"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise RPCError(f"File {path} is not the endpoint file of an RPC server: {exc}") from exc


def read_endpoint(socket_path: StrPath) -> TCPEndpoint | None:
    """Find out how to connect to the server at a socket path.

    Parameters
    ----------
    socket_path
        The path of a Unix domain socket or of an endpoint file.

    Returns
    -------
    endpoint
        The contents of the endpoint file, or `None` when the path is a Unix domain socket.

    Raises
    ------
    FileNotFoundError
        When nothing exists at the path, i.e. no server is listening there (yet).
        This is also what connecting to a Unix domain socket that does not exist raises.
    RPCError
        When the path is a file but not an endpoint file.
    """
    if stat.S_ISSOCK(os.stat(socket_path).st_mode):
        return None
    return TCPEndpoint.read(socket_path)


def set_nodelay(sock: socket.socket):
    """Send the small writes on a TCP socket right away, instead of holding them back.

    The messages of RPC calls are small and each one is waited for,
    which Nagle's algorithm combined with delayed acknowledgements stalls
    by tens to hundreds of milliseconds.
    `asyncio` sets this option for some of its TCP sockets but not for all of them,
    e.g. not for those accepted by a server on Windows (observed with Python 3.14),
    so every end of a connection sets it explicitly.

    Parameters
    ----------
    sock
        A connected TCP socket, or the `asyncio` wrapper of one.
    """
    # A connection that the peer has already closed may refuse the option,
    # which the read or write that follows reports more clearly.
    with contextlib.suppress(OSError):
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
