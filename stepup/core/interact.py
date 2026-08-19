# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Collection of tools to interact with the StepUp director.

Most of these tools are used for testing purposes.
They can also be employed to create keyboard shortcuts within your IDE,
or to interact with StepUp running in the background on a remote server.
"""

import argparse
import functools
import os
import sys
import time

from path import Path

from .api import get_rpc_client
from .config import ConfigLoader
from .constants import DIRECTOR_LOG
from .enums import ReturnCode
from .exceptions import RPCError, ToolError, UsageError
from .utils import ToolFunc, is_process_running, query_director_log

__all__ = (
    "drain_subcommand",
    "graph_subcommand",
    "join_subcommand",
    "rebuild_subcommand",
    "shutdown_subcommand",
    "wait_subcommand",
)


GET_SOCKET_TIMEOUT = 10.0
"""Seconds to wait for evidence in `DIRECTOR_LOG` that a director process exists.

This deadline only covers the gap between the TUI truncating `DIRECTOR_LOG`
and the director writing its `SOCKET` and `PID` lines into it.
Once those lines name a live process, `get_socket` waits without a deadline:
the director creates its socket only after its startup file scan,
whose duration is proportional to the size of the workflow and has no useful upper bound.
"""

GET_SOCKET_INTERVAL = 0.5
"""Seconds between two attempts to read the socket path from `DIRECTOR_LOG`."""


def _report_errors(tool: ToolFunc) -> ToolFunc:
    """Turn a failed director call into a short message on stderr.

    Upon a failed call, the wrapped tool exits with `ReturnCode.INTERNAL` instead of raising,
    so that a missing or vanished director does not confront the user with a traceback.
    """

    @functools.wraps(tool)
    def wrapper(args: argparse.Namespace):
        try:
            return tool(args)
        except (ToolError, UsageError) as exc:
            # A `UsageError` reaches this point when the director itself rejected the call.
            # It carries a short, self-contained message, so it is printed as is,
            # unlike the `RPCError` below, which is about not reaching the director at all.
            message = str(exc)
        except (ConnectionError, FileNotFoundError, RPCError) as exc:
            message = f"Could not connect to the StepUp director: {exc}"
        except TimeoutError as exc:
            message = f"Timeout while connecting to the StepUp director: {exc}"
        print(f"ERROR: {message}", file=sys.stderr)
        sys.exit(ReturnCode.INTERNAL.value)

    return wrapper


def get_socket() -> Path:
    """Block until the director socket is known and return it.

    Returns
    -------
    socket_path
        The path of the director's RPC socket.

    Raises
    ------
    ToolError
        If `DIRECTOR_LOG` did not name a live director process
        within `GET_SOCKET_TIMEOUT` seconds,
        e.g. because no director is running.
    """
    stepup_root = Path(os.getenv("STEPUP_ROOT", "."))
    director_log = stepup_root / DIRECTOR_LOG
    deadline = time.monotonic() + GET_SOCKET_TIMEOUT
    first = True
    reported_startup = False
    while True:
        socket_path, pid, message = query_director_log(director_log)
        if socket_path is not None:
            return socket_path
        if first:
            print("Trying to contact StepUp director process.", file=sys.stderr)
            first = False
        if pid is not None and is_process_running(pid):
            # The director exists but has not created its socket yet,
            # i.e. it is still busy with its startup file scan.
            # A pid recycled by an unrelated process only makes the client wait
            # instead of raising, which is harmless.
            # Say so once: this may take a while and repeating it adds nothing.
            if not reported_startup:
                print(f"StepUp director (pid {pid}) is starting up.", file=sys.stderr)
                reported_startup = True
        elif time.monotonic() >= deadline:
            raise ToolError(f"{message}  Giving up: StepUp does not seem to be running.")
        else:
            print(message, file=sys.stderr)
        time.sleep(GET_SOCKET_INTERVAL)


@_report_errors
def shutdown_tool(args: argparse.Namespace):
    """Drain the scheduler, wait for running steps to complete and then exit StepUp."""
    get_rpc_client(get_socket()).call.shutdown()


def shutdown_subcommand(subparsers, loader: ConfigLoader) -> ToolFunc:
    """Add the `shutdown` subcommand to the parser."""
    subparsers.add_parser(
        "shutdown",
        help="Drain the scheduler, wait for running steps to complete and then exit StepUp. "
        "Call again to kill running steps.",
    )
    return shutdown_tool


@_report_errors
def drain_tool(args: argparse.Namespace):
    """Drain the scheduler. (No new steps are started.)"""
    get_rpc_client(get_socket()).call.drain()


def drain_subcommand(subparsers, loader: ConfigLoader) -> ToolFunc:
    """Add the `drain` subcommand to the parser."""
    subparsers.add_parser(
        "drain",
        help="Drain the scheduler. (No new steps are started.)",
    )
    return drain_tool


@_report_errors
def join_tool(args: argparse.Namespace):
    """Wait for the builder to become idle and stop the director.

    This is the same as `stepup wait` followed by `stepup shutdown`.
    """
    get_rpc_client(get_socket()).call.wait_and_shutdown(_rpc_timeout=-1)


def join_subcommand(subparsers, loader: ConfigLoader) -> ToolFunc:
    """Add the `join` subcommand to the parser."""
    subparsers.add_parser(
        "join",
        help="Wait for the builder to become idle and stop the director.",
    )
    return join_tool


@_report_errors
def graph_tool(args: argparse.Namespace):
    """Write the workflow graph files in text and dot formats."""
    get_rpc_client(get_socket()).call.write_graph(args.prefix)


def graph_subcommand(subparsers, loader: ConfigLoader) -> ToolFunc:
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
    loader.patch_parser(parser)
    return graph_tool


@_report_errors
def rebuild_tool(args: argparse.Namespace):
    """Exit the watch phase and start the build phase."""
    get_rpc_client(get_socket()).call.start_build_phase()


def rebuild_subcommand(subparsers, loader: ConfigLoader) -> ToolFunc:
    """Add the `rebuild` subcommand to the parser."""
    subparsers.add_parser(
        "rebuild",
        help="Exit the watch phase and start the build phase.",
    )
    return rebuild_tool


@_report_errors
def wait_tool(args: argparse.Namespace):
    """Block until the builder becomes idle, or until a watched path changes."""
    client = get_rpc_client(get_socket())
    if args.update is not None:
        client.call.wait_for_update(args.update, _rpc_timeout=-1)
    elif args.delete is not None:
        client.call.wait_for_delete(args.delete, _rpc_timeout=-1)
    else:
        client.call.wait_for_idle(_rpc_timeout=-1)


def wait_subcommand(subparsers, loader: ConfigLoader) -> ToolFunc:
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
    return wait_tool
