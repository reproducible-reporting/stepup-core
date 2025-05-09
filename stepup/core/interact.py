# StepUp Core provides the basic framework for the StepUp build tool.
# © 2024–2025 Toon Verstraelen
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
"""Collection of tools to interact with the StepUp director.

Most of these tools are used for testing purposes
They can also be employed to create keyboard shortcuts within your IDE,
or to interact with StepUp running in the background on a remote server.
"""

import argparse

from rich import print  # noqa: A004

from .api import get_rpc_client
from .director import get_socket
from .file import FileState
from .step import StepState

__all__ = ()


def shutdown_tool(args: argparse.Namespace):
    """Drain the schedule. wait for running steps to complete and then exit StepUp."""
    get_rpc_client(get_socket()).call.shutdown()


def shutdown_subcommand(subparser: argparse.ArgumentParser) -> callable:
    subparser.add_parser(
        "shutdown",
        help="Drain the scheduler, wait for running steps to complete and then exit StepUp. "
        "Call again to kill running steps.",
    )
    return shutdown_tool


def drain_tool(args: argparse.Namespace):
    """Drain the scheduler. (No new steps are started.)"""
    get_rpc_client(get_socket()).call.drain()


def drain_subcommand(subparser: argparse.ArgumentParser) -> callable:
    subparser.add_parser(
        "drain",
        help="Drain the scheduler. (No new steps are started.)",
    )
    return drain_tool


def join_tool(args: argparse.Namespace):
    """Wait for the runner to become idle and stop the director.

    This is the same as `wait()` followed by `shutdown()`."""
    get_rpc_client(get_socket()).call.join(_rpc_timeout=-1)


def join_subcommand(subparser: argparse.ArgumentParser) -> callable:
    subparser.add_parser(
        "join",
        help="Wait for the runner to become idle and stop the director.",
    )
    return join_tool


def graph_tool(args: argparse.Namespace):
    """Write the workflow graph files in text and dot formats."""
    get_rpc_client(get_socket()).call.graph(args.prefix)


def graph_subcommand(subparser: argparse.ArgumentParser) -> callable:
    parser = subparser.add_parser(
        "graph",
        help="Write the workflow graph files in text and dot formats.",
    )
    parser.add_argument(
        "prefix",
        help="Prefix for the output files. The files will be named <prefix>.txt and <prefix>.dot.",
    )
    return graph_tool


def status_tool(args: argparse.Namespace):
    """Print the status of the director."""
    status = get_rpc_client(get_socket()).call.status()
    print("[bold underline]Step counts[/]")
    for value, count in status["step_counts"].items():
        print(f"  {StepState(value).name:10s} {count:6d}")
    print()
    print("[bold underline]File counts[/]")
    for value, count in status["file_counts"].items():
        print(f"  {FileState(value).name:10s} {count:6d}")
    print()
    print("[bold underline]Running steps[/]")
    for action in status["running_steps"]:
        print(f"  {action}")


def status_subcommand(subparser: argparse.ArgumentParser) -> callable:
    subparser.add_parser(
        "status",
        help="Print the status of the director.",
    )
    return status_tool


def run_tool(args: argparse.Namespace):
    """Exit the watch phase and start running steps whose inputs have changed."""
    get_rpc_client(get_socket()).call.run()


def run_subcommand(subparser: argparse.ArgumentParser) -> callable:
    subparser.add_parser(
        "run",
        help="Exit the watch phase and start running steps whose inputs have changed.",
    )
    return run_tool


def watch_update_tool(args: argparse.Namespace):
    """Block until the watcher has observed an update of the file."""
    get_rpc_client(get_socket()).call.watch_update(args.path, _rpc_timeout=-1)


def watch_update_subcommand(subparser: argparse.ArgumentParser) -> callable:
    parser = subparser.add_parser(
        "watch-update",
        help="Block until the watcher has observed an update of the file.",
    )
    parser.add_argument(
        "path",
        help="Path to the file to watch.",
    )
    return watch_update_tool


def watch_delete_tool(args: argparse.Namespace):
    """Block until the watcher has observed the deletion of the file."""
    get_rpc_client(get_socket()).call.watch_delete(args.path, _rpc_timeout=-1)


def watch_delete_subcommand(subparser: argparse.ArgumentParser) -> callable:
    parser = subparser.add_parser(
        "watch-delete",
        help="Block until the watcher has observed the deletion of the file.",
    )
    parser.add_argument(
        "path",
        help="Path to the file to watch.",
    )
    return watch_delete_tool


def wait_tool(args: argparse.Namespace):
    """Block until the runner has become idle."""
    get_rpc_client(get_socket()).call.wait(_rpc_timeout=-1)


def wait_subcommand(subparser: argparse.ArgumentParser) -> callable:
    subparser.add_parser(
        "wait",
        help="Block until the runner has become idle.",
    )
    return wait_tool
