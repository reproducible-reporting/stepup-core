# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Collection of tools to interact with the StepUp director.

Most of these tools are used for testing purposes
They can also be employed to create keyboard shortcuts within your IDE,
or to interact with StepUp running in the background on a remote server.
"""

import argparse
import contextlib
import functools
import os
import sys
import time
from collections.abc import Callable

from path import Path

from .api import get_rpc_client
from .config import ConfigLoader
from .constants import DIRECTOR_LOG
from .enums import ReturnCode
from .exceptions import InteractError, RPCError

__all__ = ()


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


def _report_errors(tool: Callable) -> Callable:
    """Turn an error while contacting the director into a short message on stderr.

    The wrapped tool exits with `ReturnCode.INTERNAL` instead of raising,
    so that a missing or vanished director does not confront the user with a traceback.
    """

    @functools.wraps(tool)
    def wrapper(args: argparse.Namespace):
        try:
            return tool(args)
        except InteractError as exc:
            message = str(exc)
        except (ConnectionError, FileNotFoundError, RPCError) as exc:
            message = f"Could not connect to the StepUp director: {exc}"
        except TimeoutError as exc:
            message = f"Timeout while connecting to the StepUp director: {exc}"
        print(f"ERROR: {message}", file=sys.stderr)
        sys.exit(ReturnCode.INTERNAL.value)

    return wrapper


def _is_running(pid: int) -> bool:
    """Whether a process with this pid exists (it may or may not be the director)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # The process exists but belongs to another user.
        return True
    return True


def _query_director_log(path_director_log: Path) -> tuple[Path | None, int | None, str]:
    """Look up the director's socket and pid in `DIRECTOR_LOG`.

    Parameters
    ----------
    path_director_log
        The path of the director log to read from.

    Returns
    -------
    path_socket
        The socket path advertised by the director, if it exists on disk, `None` otherwise.
    pid
        The pid advertised by the director,
        or `None` when the log holds no usable `PID` line.
    message
        An explanation of why no existing socket was found, empty when one was.
    """
    if not os.path.isfile(path_director_log):
        return None, None, f"File {path_director_log} not found."
    with open(path_director_log) as fh:
        line_socket = fh.readline()
        line_pid = fh.readline()

    # A non-empty path is the only degenerate case worth guarding:
    # `async_main` writes each line in one shot (`sys.stderr` is line-buffered), so a short
    # but non-empty tail cannot occur in practice. Reading them from the same file handle can
    # at worst miss the `PID` line of a director that has just written the `SOCKET` line,
    # which the next attempt picks up.
    pid = None
    if line_pid.startswith("PID"):
        with contextlib.suppress(ValueError):
            pid = int(line_pid[3:])

    if not line_socket.startswith("SOCKET"):
        return None, pid, f"File {path_director_log} does not start with SOCKET line."
    path_socket = Path(line_socket[6:].strip())
    if path_socket and path_socket.exists():
        return path_socket, pid, ""
    message = (
        f"Socket {path_socket} read from {path_director_log} does not exist. StepUp not running?"
    )
    return None, pid, message


def get_socket() -> Path:
    """Block until the director socket is known and return it.

    The wait is only bounded by `GET_SOCKET_TIMEOUT` as long as `DIRECTOR_LOG`
    shows no sign of a live director process.
    A director that is still scanning files at startup may take much longer than that
    to create its socket, and is waited for without a deadline.

    Returns
    -------
    path_socket
        The path of the director's RPC socket.

    Raises
    ------
    InteractError
        If `DIRECTOR_LOG` did not name a live director process
        within `GET_SOCKET_TIMEOUT` seconds,
        e.g. because no director is running.
    """
    stepup_root = Path(os.getenv("STEPUP_ROOT", "."))
    path_director_log = stepup_root / DIRECTOR_LOG
    deadline = time.monotonic() + GET_SOCKET_TIMEOUT
    first = True
    reported_startup = False
    while True:
        path_socket, pid, message = _query_director_log(path_director_log)
        if path_socket is not None:
            return path_socket
        if first:
            print("Trying to contact StepUp director process.", file=sys.stderr)
            first = False
        if pid is not None and _is_running(pid):
            # The director exists but has not created its socket yet,
            # i.e. it is still busy with its startup file scan.
            # A pid recycled by an unrelated process only makes the client wait
            # instead of raising, which is harmless.
            # Say so once: this may take a while and repeating it adds nothing.
            if not reported_startup:
                print(f"StepUp director (pid {pid}) is starting up.", file=sys.stderr)
                reported_startup = True
        elif time.monotonic() >= deadline:
            raise InteractError(f"{message}  Giving up: StepUp does not seem to be running.")
        else:
            print(message, file=sys.stderr)
        time.sleep(GET_SOCKET_INTERVAL)


@_report_errors
def shutdown_tool(args: argparse.Namespace):
    """Put the scheduler on hold, wait for running steps to complete and then exit StepUp."""
    get_rpc_client(get_socket()).call.shutdown()


def shutdown_subcommand(subparsers, loader: ConfigLoader) -> Callable:
    subparsers.add_parser(
        "shutdown",
        help="Put the scheduler on hold, wait for running steps to complete and then exit StepUp. "
        "Call again to kill running steps.",
    )
    return shutdown_tool


@_report_errors
def drain_tool(args: argparse.Namespace):
    """Put the scheduler on hold. (No new steps are started.)"""
    get_rpc_client(get_socket()).call.drain()


def drain_subcommand(subparsers, loader: ConfigLoader) -> Callable:
    subparsers.add_parser(
        "drain",
        help="Put the scheduler on hold. (No new steps are started.)",
    )
    return drain_tool


@_report_errors
def join_tool(args: argparse.Namespace):
    """Wait for the builder to become idle and stop the director.

    This is the same as `wait()` followed by `shutdown()`."""
    get_rpc_client(get_socket()).call.join(_rpc_timeout=-1)


def join_subcommand(subparsers, loader: ConfigLoader) -> Callable:
    subparsers.add_parser(
        "join",
        help="Wait for the builder to become idle and stop the director.",
    )
    return join_tool


@_report_errors
def graph_tool(args: argparse.Namespace):
    """Write the workflow graph files in text and dot formats."""
    get_rpc_client(get_socket()).call.graph(args.prefix)


def graph_subcommand(subparsers, loader: ConfigLoader) -> Callable:
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
def run_tool(args: argparse.Namespace):
    """Exit the watch phase and start the build phase."""
    get_rpc_client(get_socket()).call.run()


def run_subcommand(subparsers, loader: ConfigLoader) -> Callable:
    subparsers.add_parser(
        "run",
        help="Exit the watch phase and start the build phase.",
    )
    return run_tool


@_report_errors
def watch_update_tool(args: argparse.Namespace):
    """Block until the watcher has observed an update of the file."""
    get_rpc_client(get_socket()).call.watch_update(args.path, _rpc_timeout=-1)


def watch_update_subcommand(subparsers, loader: ConfigLoader) -> Callable:
    parser = subparsers.add_parser(
        "watch-update",
        help="Block until the watcher has observed an update of the file.",
    )
    parser.add_argument(
        "path",
        help="Path to the file to watch.",
    )
    return watch_update_tool


@_report_errors
def watch_delete_tool(args: argparse.Namespace):
    """Block until the watcher has observed the deletion of the file."""
    get_rpc_client(get_socket()).call.watch_delete(args.path, _rpc_timeout=-1)


def watch_delete_subcommand(subparsers, loader: ConfigLoader) -> Callable:
    parser = subparsers.add_parser(
        "watch-delete",
        help="Block until the watcher has observed the deletion of the file.",
    )
    parser.add_argument(
        "path",
        help="Path to the file to watch.",
    )
    return watch_delete_tool


@_report_errors
def wait_tool(args: argparse.Namespace):
    """Block until the builder has become idle."""
    get_rpc_client(get_socket()).call.wait(_rpc_timeout=-1)


def wait_subcommand(subparsers, loader: ConfigLoader) -> Callable:
    subparsers.add_parser(
        "wait",
        help="Block until the builder has become idle.",
    )
    return wait_tool
