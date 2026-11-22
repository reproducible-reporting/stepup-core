<!--
SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
SPDX-License-Identifier: LGPL-3.0-or-later
-->

# RPC Between the StepUp Processes

This file documents `stepup/core/rpc/`.
The layering of the subpackages and the process model are in `stepup/core/CLAUDE.md`.

## Transports (`transport.py`, `server.py`)

The RPC servers listen on one of two transports,
which the TUI selects once with `select_rpc_transport()` (`STEPUP_RPC_TRANSPORT`)
and passes to the director with `--rpc-transport`:
a Unix domain socket where `socket.AF_UNIX` exists (the default),
or a TCP socket on `127.0.0.1` (the default and only choice on Windows).
Either way, a server is known by a *socket path*:
the Unix domain socket itself, or an endpoint file holding the port and a random token.
Either one appears atomically once the server listens,
so "the path exists" means "the server accepts connections" for both transports,
which is what the readiness check of the TUI and `query_director_log` rely on.
A Unix domain socket needs a rename for this (`SocketRPCServer._listen_unix`),
because `bind()` creates the socket file before `listen()`,
and a client that connects in between is refused.
Clients (steps, `stepup` subcommands, the director's reporter client)
tell the transports apart by the file type at the socket path,
so they need no setting, and `STEPUP_DIRECTOR_SOCKET` and the `SOCKET` line of the
director log have the same shape for both.

A client does not wait for a server that is not there yet:
connecting to a socket path where nothing exists fails at once with a `FileNotFoundError`.
Whoever starts a process that connects to a server therefore awaits `SocketRPCServer.listen()`
before it starts that process.
Leaving the listening to the task that runs `SocketRPCServer.serve()` is not enough,
because that task may not have run yet when the process connects.

A TCP server accepts connections with `_TCPAcceptor` instead of `asyncio.start_server`,
which the Proactor event loop of Windows stops after one connection reset before its accept.
Its docstring says when it can be dropped.

The request bodies are unpickled, so whoever can connect can run code in the server.
A TCP client therefore sends the token before anything else,
and the server reads nothing more from a connection with a wrong token.
The private temporary directory is the trust boundary for both transports.
On Windows, the mode of the endpoint file has no effect,
and the directory only has access rules of its own from Python 3.11.10 and 3.12.4 on
(CVE-2024-4030, checked in the VM on 2026-11-15).
With older versions, it inherits those of `TEMP`, which are private only by default.
Never put the token in an environment variable or on a command line.
