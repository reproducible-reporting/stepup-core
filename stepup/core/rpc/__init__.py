# SPDX-FileCopyrightText: 2026 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Lightweight and versatile RPC implementation using asyncio stream reader and writer.

There are asynchronous and synchronous clients, the latter to support simple client APIs.

A server is given a handler: any object whose methods, decorated with `@allow_rpc`,
a client may call remotely.
Only decorated methods can be called, which bounds what a client may ask for by name.

The bodies of requests and responses are unpickled,
so whoever can connect to a server can execute code in the process at the other end,
whether a method allows it or not.
A server listens on a Unix domain socket or on a TCP socket, see `select_rpc_transport`.
A Unix domain socket admits the processes that can access its path.
A TCP socket on the loopback interface admits every local user,
so the server first reads a token from each new connection,
and closes the connection without reading anything else when the token is wrong.
Clients read the token from an endpoint file, see `stepup.core.rpc.transport`.
Either way, the permissions of the directory holding the socket or the endpoint file
are the trust boundary,
so that directory must be accessible only to the user running the server.
"""

__all__ = ()
