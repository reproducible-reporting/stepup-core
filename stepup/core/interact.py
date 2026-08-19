# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Collection of tools to interact with the StepUp director.

They can be employed to create keyboard shortcuts within your IDE,
or to interact with StepUp running in the background on a remote server.
"""

import argparse
import contextlib
import sys
import time
from collections.abc import Generator

from path import Path

from .api import get_rpc_client
from .config_loader import ConfigLoader
from .constants import DIRECTOR_LOG
from .exceptions import RPCError, ToolError
from .path import get_stepup_root
from .rpc import SocketSyncRPCClient
from .tool import SubParsers, ToolFunc
from .utils import is_process_running, query_director_log

__all__ = (
    "add_drain_subcommand",
    "add_graph_subcommand",
    "add_join_subcommand",
    "add_rebuild_subcommand",
    "add_shutdown_subcommand",
    "add_wait_subcommand",
)


#
# Connecting to the director
#


WAIT_FOR_SOCKET_TIMEOUT = 10.0
"""Seconds to wait for evidence in `DIRECTOR_LOG` that a director process exists.

This deadline only covers the gap between the TUI truncating `DIRECTOR_LOG`
and the director writing its `SOCKET` and `PID` lines into it.
Once those lines name a live process, `wait_for_director_socket` waits without a deadline:
the director creates its socket only after its startup file scan,
whose duration is proportional to the size of the workflow and has no useful upper bound.
"""

WAIT_FOR_SOCKET_INTERVAL = 0.5
"""Seconds between two attempts to read the socket path from `DIRECTOR_LOG`."""

NO_RPC_TIMEOUT = -1.0
"""The `_rpc_timeout` of a call that the director answers only when the workflow is ready for it.

How long that takes is a property of the workflow, not of the connection,
so the default timeout of the RPC client would only cut off a healthy wait.
"""


def wait_for_director_socket() -> Path:
    """Block until the director socket is known and return it.

    Returns
    -------
    socket_path
        The path of the director's RPC socket.

    Raises
    ------
    ToolError
        If `DIRECTOR_LOG` did not name a live director process
        within `WAIT_FOR_SOCKET_TIMEOUT` seconds,
        e.g. because no director is running.
    """
    director_log = get_stepup_root() / DIRECTOR_LOG
    deadline = time.monotonic() + WAIT_FOR_SOCKET_TIMEOUT
    # The last thing said about the situation, `None` as long as nothing was said at all.
    reported = None
    while True:
        socket_path, pid, message = query_director_log(director_log)
        if socket_path is not None:
            return socket_path
        if reported is None:
            print("Trying to contact StepUp director process.", file=sys.stderr)
        if pid is not None and is_process_running(pid):
            # The director exists but has not created its socket yet,
            # i.e. it is still busy with its startup file scan.
            # A pid recycled by an unrelated process only makes the client wait
            # instead of raising, which is harmless.
            message = f"StepUp director (pid {pid}) is starting up."
        elif time.monotonic() >= deadline:
            raise ToolError(f"{message}  Giving up: StepUp does not seem to be running.")
        # Only report a situation that differs from the last one reported:
        # the same line repeated every `WAIT_FOR_SOCKET_INTERVAL` seconds adds nothing.
        if message != reported:
            print(message, file=sys.stderr)
            reported = message
        time.sleep(WAIT_FOR_SOCKET_INTERVAL)


@contextlib.contextmanager
def _connect_director() -> Generator[SocketSyncRPCClient]:
    """Wait for the director and hand out a client for a single conversation with it.

    The socket path is always known here, so the client is never the dummy one
    that `get_rpc_client` hands out when there is no director to talk to.
    The connection is opened by the first call made on the client.

    Yields
    ------
    client
        The client to call the director's remote procedures with.

    Raises
    ------
    ToolError
        If the director could not be reached.
        A `UsageError` raised by the director itself passes through:
        the call did reach the director, which rejected it with a message of its own.
    """
    client = get_rpc_client(wait_for_director_socket())
    try:
        yield client
    except (ConnectionError, FileNotFoundError, RPCError) as exc:
        raise ToolError(f"Could not connect to the StepUp director: {exc}") from exc
    except TimeoutError as exc:
        raise ToolError(f"Timeout while connecting to the StepUp director: {exc}") from exc
    # Closing tells the director's side of the connection to wind down,
    # which is a courtesy and not a requirement.
    # A director that has just accepted a shutdown may be gone before the message arrives,
    # so a broken connection at this point is not worth reporting.
    with contextlib.suppress(OSError):
        client.close()


#
# Subcommands
#


def add_drain_subcommand(subparsers: SubParsers, loader: ConfigLoader) -> ToolFunc:
    """Add the `drain` subcommand to the parser."""
    subparsers.add_parser(
        "drain",
        help="Drain the scheduler. (No new steps are started.)",
    )

    def drain_tool(args: argparse.Namespace) -> None:
        with _connect_director() as client:
            client.call.drain()

    return drain_tool


def add_graph_subcommand(subparsers: SubParsers, loader: ConfigLoader) -> ToolFunc:
    """Add the `graph` subcommand to the parser."""
    parser = subparsers.add_parser(
        "graph",
        help="Write the workflow graph files in text and dot formats.",
    )
    parser.add_argument(
        "prefix",
        help="Prefix for the output files. The files will be named "
        "<prefix>.txt, <prefix>_provenance.dot, and <prefix>_dependency.dot.",
    )

    def graph_tool(args: argparse.Namespace) -> None:
        with _connect_director() as client:
            client.call.write_graph(args.prefix)

    return graph_tool


def add_join_subcommand(subparsers: SubParsers, loader: ConfigLoader) -> ToolFunc:
    """Add the `join` subcommand to the parser."""
    subparsers.add_parser(
        "join",
        help="Wait for the builder to become idle and stop the director.",
    )

    def join_tool(args: argparse.Namespace) -> None:
        """Do the same as `stepup wait` followed by `stepup shutdown`, in one call."""
        with _connect_director() as client:
            client.call.wait_and_shutdown(_rpc_timeout=NO_RPC_TIMEOUT)

    return join_tool


def add_rebuild_subcommand(subparsers: SubParsers, loader: ConfigLoader) -> ToolFunc:
    """Add the `rebuild` subcommand to the parser."""
    subparsers.add_parser(
        "rebuild",
        help="Exit the watch phase and start the build phase.",
    )

    def rebuild_tool(args: argparse.Namespace) -> None:
        with _connect_director() as client:
            client.call.start_build_phase()

    return rebuild_tool


def add_shutdown_subcommand(subparsers: SubParsers, loader: ConfigLoader) -> ToolFunc:
    """Add the `shutdown` subcommand to the parser."""
    subparsers.add_parser(
        "shutdown",
        help="Drain the scheduler, wait for running steps to complete and then exit StepUp. "
        "Call again to kill running steps.",
    )

    def shutdown_tool(args: argparse.Namespace) -> None:
        with _connect_director() as client:
            client.call.shutdown()

    return shutdown_tool


def add_wait_subcommand(subparsers: SubParsers, loader: ConfigLoader) -> ToolFunc:
    """Add the `wait` subcommand to the parser."""
    parser = subparsers.add_parser(
        "wait",
        help="Block until the builder becomes idle, or until a watched path changes.",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "-u",
        "--update",
        metavar="PATH",
        default=None,
        help="Block until the watcher has observed an update of PATH, "
        "instead of waiting for the builder to become idle.",
    )
    group.add_argument(
        "-d",
        "--delete",
        metavar="PATH",
        default=None,
        help="Block until the watcher has observed the deletion of PATH, "
        "instead of waiting for the builder to become idle.",
    )

    def wait_tool(args: argparse.Namespace) -> None:
        with _connect_director() as client:
            if args.update is not None:
                client.call.wait_for_update(args.update, _rpc_timeout=NO_RPC_TIMEOUT)
            elif args.delete is not None:
                client.call.wait_for_delete(args.delete, _rpc_timeout=NO_RPC_TIMEOUT)
            else:
                client.call.wait_for_idle(_rpc_timeout=NO_RPC_TIMEOUT)

    return wait_tool
