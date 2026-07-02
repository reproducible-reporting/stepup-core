# StepUp Core provides the basic framework for the StepUp build tool.
# Copyright 2024-2026 Toon Verstraelen
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
"""StepUp terminal user interface."""

import argparse
import asyncio
import contextlib
import os
import subprocess
import sys
import tempfile
import termios
import threading
from collections.abc import Callable
from decimal import Decimal
from functools import partial

from path import Path

from .asyncio import stoppable_iterator, wait_for_path
from .config import ConfigLoader
from .constants import DIRECTOR_LOG, PERF_DATA, SQLLOG_JSON, STEPUP_DIR
from .director import interpret_jobs
from .reporter import ReporterHandler
from .rpc import AsyncRPCClient, serve_socket_rpc
from .utils import parse_resources
from .watcher import WATCHER_AVAILABLE

__all__ = ()


def merge_resources(base: str | None, override: str | None) -> str:
    merged = {**parse_resources(base or ""), **parse_resources(override or "")}
    return ",".join(f"{k}:{v}" for k, v in merged.items())


def build_tool(args: argparse.Namespace, default_resources: str):
    asyncio.run(async_build(args, default_resources))


async def async_build(args: argparse.Namespace, default_resources: str):
    if WATCHER_AVAILABLE and args.watch_first:
        args.watch = True
    stepup_root = Path(os.getenv("STEPUP_ROOT", os.getcwd()))
    if stepup_root.absolute() != Path.cwd():
        print("Changing to", stepup_root)
        stepup_root.cd()

    # Sanity check before creating a subdirectory.
    if not Path("plan.py").is_file():
        raise RuntimeError("File plan.py does not exist.")

    # Check if another StepUp director is already/still running.
    if DIRECTOR_LOG.exists():
        with open(DIRECTOR_LOG) as fh:
            try:
                line = next(fh)
                path_old_socket = line.split()[-1]
                if Path(path_old_socket).exists():
                    raise RuntimeError(
                        f"Old director still running? Socket still exists: {path_old_socket}"
                    )
            except StopIteration:
                pass

    # Create dir
    dir_stepup = STEPUP_DIR
    dir_stepup.makedirs_p()
    for path_log in dir_stepup.glob("*.log"):
        path_log.remove_p()

    with tempfile.TemporaryDirectory(prefix="stepup-") as dir_sockets:
        dir_sockets = Path(dir_sockets)

        # Create socket paths
        director_socket_path = dir_sockets / "director"
        reporter_socket_path = dir_sockets / "reporter"

        # Set up the reporter monitor
        stop_event = asyncio.Event()
        reporter_handler = ReporterHandler(args.show_perf > 0, args.progress, stop_event)
        task_reporter = asyncio.create_task(
            serve_socket_rpc(reporter_handler, reporter_socket_path, stop_event),
            name="reporter-rpc",
        )
        tasks = [task_reporter]

        # Launch director as background process
        njob = interpret_jobs(args.jobs)
        argv = []
        if args.perf is not None:
            argv.extend(["perf", "record", "-F", str(args.perf), "-i", "-g", "-o", PERF_DATA])
        argv.append(sys.executable)
        if args.perf is not None:
            argv.extend(["-X", "perf"])
        argv.extend(
            [
                "-m",
                "stepup.core.director",
                director_socket_path,
                f"--reporter={reporter_socket_path}",
                f"--jobs={njob}",
                f"--log-level={args.log_level}",
            ]
        )
        if args.forkserver:
            argv.append("--forkserver")
        if args.preload_modules:
            argv.append(f"--preload-modules={args.preload_modules}")
        if not args.clean:
            argv.append("--no-clean")
        if not args.duration:
            argv.append("--no-duration")
        if args.explain_rerun:
            argv.append("--explain-rerun")
        if not args.fix_epoch:
            argv.append("--no-fix-epoch")
        if args.show_perf > 1:
            argv.append("--show-perf")
        args.resources = merge_resources(default_resources, args.resources)
        if args.resources:
            argv.append(f"--resources={args.resources}")
        if WATCHER_AVAILABLE:
            if args.watch:
                argv.append("--watch")
            if args.watch_first:
                argv.append("--watch-first")
        if args.yappi:
            argv.append("--yappi")
        if args.sqllog:
            argv.append("--sqllog")
        returncode = 1  # Internal error unless it is overriden later by the director subprocess
        try:
            with open(DIRECTOR_LOG, "w") as log_file:
                process_director = await asyncio.create_subprocess_exec(
                    *argv,
                    stdin=subprocess.DEVNULL,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                )
                # Instantiate keyboard interaction or work non-interactively
                if sys.stdin.isatty():
                    await wait_for_path(director_socket_path, stop_event)
                    task_keyboard = asyncio.create_task(
                        keyboard(director_socket_path, reporter_handler, stop_event),
                        name="keyboard",
                    )
                    tasks.append(task_keyboard)
                returncode = await process_director.wait()
            stop_event.set()
        finally:
            try:
                await asyncio.gather(*tasks)
            except ConnectionRefusedError:
                reporter_handler.report("ERROR", "Could not connect to director", [])

    # Check if the director.log file has error messages
    with open(DIRECTOR_LOG) as fh:
        if any(("ERROR" in line) or ("CRITICAL" in line) for line in fh):
            reporter_handler.report("WARNING", f"Errors logged in {DIRECTOR_LOG}", [])

    sys.exit(returncode)


@contextlib.asynccontextmanager
async def iter_keystrokes(stop_event: asyncio.Event):
    """Yield keystrokes from stdin, one at a time, in raw mode, until `stop_event` is set.

    Reads happen in a background thread because putting stdin in non-blocking mode
    (e.g. via `asyncio.StreamReader`) also affects stdout and stderr when they share the
    same underlying open file description as stdin, which breaks `print` and `rich.print`
    for large output.
    """
    fd = sys.stdin.fileno()
    old_tcattr = termios.tcgetattr(fd)
    new_tcattr = termios.tcgetattr(fd)
    new_tcattr[3] &= ~(termios.ICANON | termios.ECHO)
    termios.tcsetattr(fd, termios.TCSAFLUSH, new_tcattr)
    loop = asyncio.get_running_loop()
    queue = asyncio.Queue()

    def _stdin_loop():
        while True:
            loop.call_soon_threadsafe(queue.put_nowait, sys.stdin.read(1))

    threading.Thread(target=_stdin_loop, daemon=True).start()
    try:
        yield stoppable_iterator(queue.get, stop_event)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_tcattr)


KEY_STROKE_HELP = """
   r = run     q = shutdown     d = drain     j = join     g = graph
"""


async def keyboard(
    director_socket_path: Path,
    reporter_handler: ReporterHandler,
    stop_event: asyncio.Event,
):
    async with iter_keystrokes(stop_event) as keys:
        async for ch in keys:
            if ch in "qjdrg":
                async with await AsyncRPCClient.socket(director_socket_path) as client:
                    if ch == "q":
                        reporter_handler.report("KEYBOARD", "Shutting down", [])
                        await client.call.shutdown()
                    elif ch == "j":
                        reporter_handler.report(
                            "KEYBOARD", "Waiting for all steps before shutdown.", []
                        )
                        await client.call.join()
                    elif ch == "d":
                        reporter_handler.report("KEYBOARD", "Putting the scheduler on hold.", [])
                        await client.call.drain()
                    elif ch == "r":
                        reporter_handler.report("KEYBOARD", "Restarting the builder.", [])
                        await client.call.run()
                    elif ch == "g":
                        await client.call.graph("graph")
                        reporter_handler.report(
                            "KEYBOARD", "Workflow graph written to graph.txt.", []
                        )
            else:
                pages = [("Keys", KEY_STROKE_HELP)]
                reporter_handler.report("KEYBOARD", f"Unsupported key {ch}", pages)


def _add_build_parser(subparsers, loader: ConfigLoader, name: str, help_text: str) -> str:
    """Register the build subparser under *name* and return its default resources.

    The argument definitions are identical for every subcommand name; only the
    subparser name and its help text differ. Configuration always comes from the
    `"build"` section, regardless of *name*, so `stepup build` and its aliases
    share a single source of truth for configuration.

    Parameters
    ----------
    subparsers
        The sub parser to add the build tool to.
    loader
        The configuration loader to override the default configuration with
        config file values.
    name
        The subcommand name to register (e.g. `"build"` or `"boot"`).
    help_text
        The help text shown for this subcommand.

    Returns
    -------
    default_resources
        The default value of the `--resources` argument, used to seed
        `merge_resources` in the build tool.
    """
    parser = subparsers.add_parser(
        name,
        prog=name,
        help=help_text,
    )
    parser.add_argument(
        "--clean",
        default=True,
        action=argparse.BooleanOptionalAction,
        help="Remove outdated output files.",
    )
    parser.add_argument(
        "--duration",
        default=True,
        action=argparse.BooleanOptionalAction,
        help="Use the duration of steps to optimize the execution order.",
    )
    parser.add_argument(
        "--explain-rerun",
        "-e",
        default=False,
        action=argparse.BooleanOptionalAction,
        help="Explain for every step with recording info why it cannot be skipped.",
    )
    parser.add_argument(
        "--jobs",
        "-j",
        type=Decimal,
        default=Decimal("1.2"),
        help="Number of parallel jobs. "
        "When given as a real number with digits after the comma, "
        "it is multiplied with the number of available cores. [default=%(default)s]",
    )
    parser.add_argument(
        "--fix-epoch",
        default=True,
        action=argparse.BooleanOptionalAction,
        help="Set the SOURCE_DATE_EPOCH environment variable to 315532800. "
        "This corresponds to 1980-01-01 00:00:00 UTC. "
        "(If the variable is already set, it will be used as-is.) ",
    )
    parser.add_argument(
        "--forkserver",
        default=(sys.platform == "linux"),
        action=argparse.BooleanOptionalAction,
        help="Use a forkserver for Python step execution and file hashing "
        "to reduce startup overhead. [default: True on Linux, False elsewhere]",
    )
    parser.add_argument(
        "--preload-modules",
        default=None,
        help="Comma-separated list of Python modules to pre-load into the forkserver. "
        "Only has effect when --forkserver is active. [default: none]",
    )
    parser.add_argument(
        "--progress",
        default=True,
        action=argparse.BooleanOptionalAction,
        help="Report progress information in the terminal user interface. "
        "(This can be useful to simplify and reduce the output.)",
    )
    parser.add_argument(
        "--perf",
        default=None,
        nargs="?",
        const="500",
        help="Profile the director with perf, by default at a frequency of %(const)s Hz. "
        "(Only supported on Linux with perf installed.)",
    )
    parser.add_argument(
        "--show-perf",
        "-s",
        default=0,
        action="count",
        help="Show the performance info on each line. Repeat for more detailed info.",
    )
    resources_action = parser.add_argument(
        "--resources",
        "-r",
        type=str,
        default=None,
        help="Available resources for steps, e.g. 'cpu:4,gpu:1,memgb:16'. "
        "Merged with (not overriding) config files and STEPUP_BUILD_RESOURCES env var.",
    )
    parser.add_argument(
        "--sqllog",
        default=False,
        action=argparse.BooleanOptionalAction,
        help=f"Enable SQLite debug logging and write the recorded log to {SQLLOG_JSON} "
        "when the director exits.",
    )
    if WATCHER_AVAILABLE:
        parser.add_argument(
            "--watch",
            "-w",
            default=False,
            action=argparse.BooleanOptionalAction,
            help="StepUp will watch for file changes after all runnable steps have been executed. "
            "By pressing r, it will rerun steps  that have become pending due to the file changes. "
            "(Only supported on Linux.)",
        )
        parser.add_argument(
            "--watch-first",
            "-W",
            default=False,
            action=argparse.BooleanOptionalAction,
            help="Start the builder after observing the first file change in watch mode. "
            "This implies --watch. (Only supported on Linux.)",
        )
    parser.add_argument(
        "--yappi",
        default=False,
        action=argparse.BooleanOptionalAction,
        help="Profile the director with Yappi (must be installed). "
        "This produces a .stepup/director.prof file that can be analyzed with "
        "tools like SnakeViz.",
    )
    loader.patch_parser(parser, merge_handlers={"resources": merge_resources})
    return resources_action.default


def build_subcommand(subparsers, loader: ConfigLoader) -> Callable:
    """Define command-line arguments for the build tool.

    Parameters
    ----------
    subparsers
        The sub parser to add the build tool to.
    loader
        The configuration loader to override the default configuration with
        config file values.
    """
    default_resources = _add_build_parser(subparsers, loader, "build", "Build the StepUp workflow.")
    return partial(build_tool, default_resources=default_resources)


def boot_subcommand(subparsers, loader: ConfigLoader) -> Callable:
    """Define command-line arguments for the deprecated `boot` alias of `build`.

    Parameters
    ----------
    subparsers
        The sub parser to add the boot tool to.
    loader
        The configuration loader to override the default configuration with
        config file values.
    """
    default_resources = _add_build_parser(
        subparsers, loader, "boot", "Deprecated alias of 'stepup build'."
    )
    return partial(_deprecated_boot_tool, default_resources=default_resources)


def _deprecated_boot_tool(args: argparse.Namespace, default_resources: str):
    print(
        "Warning: 'stepup boot' is deprecated; use 'stepup build' instead.",
        file=sys.stderr,
    )
    build_tool(args, default_resources=default_resources)
