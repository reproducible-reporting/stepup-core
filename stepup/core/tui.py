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
"""StepUp terminal user interface."""

import argparse
import asyncio
import subprocess
import sys
import tempfile
import termios
import threading
from decimal import Decimal

import attrs
from path import Path

from .asyncio import stoppable_iterator, wait_for_path
from .director import interpret_num_workers
from .reporter import ReporterClient, ReporterHandler
from .rpc import AsyncRPCClient, serve_socket_rpc
from .watcher import WATCHER_AVAILABLE

__all__ = ()


def boot_tool(args: argparse.Namespace):
    asyncio.run(async_boot(args))


async def async_boot(args: argparse.Namespace):
    if WATCHER_AVAILABLE and args.watch_first:
        args.watch = True
    if args.root.absolute() != Path.cwd():
        print("Changing to", args.root)
        args.root.cd()

    # Sanity check before creating a subdirectory.
    if not Path("plan.py").is_file():
        raise RuntimeError("File plan.py does not exist.")

    # Check if another StepUp director is already/still running.
    if Path(".stepup/director.log").exists():
        with open(".stepup/director.log") as fh:
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
    dir_stepup = Path(".stepup")
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
        num_workers = interpret_num_workers(args.num_workers)
        argv = []
        if args.perf is not None:
            argv.extend(
                ["perf", "record", "-F", str(args.perf), "-i", "-g", "-o", ".stepup/perf.data"]
            )
        argv.append(sys.executable)
        if args.perf is not None:
            argv.extend(["-X", "perf"])
        argv.extend(
            [
                "-m",
                "stepup.core.director",
                director_socket_path,
                f"--reporter={reporter_socket_path}",
                f"--num-workers={num_workers}",
                f"--log-level={args.log_level}",
            ]
        )
        if args.show_perf > 1:
            argv.append("--show-perf")
        if args.explain_rerun:
            argv.append("--explain-rerun")
        if WATCHER_AVAILABLE:
            if args.watch:
                argv.append("--watch")
            if args.watch_first:
                argv.append("--watch-first")
        if not args.do_clean:
            argv.append("--no-clean")
        returncode = 1  # Internal error unless it is overriden later by the director subprocess
        try:
            with open(".stepup/director.log", "w") as log_file:
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
                        keyboard(director_socket_path, reporter_socket_path, stop_event),
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
    with open(".stepup/director.log") as fh:
        if any(("ERROR" in line) or ("CRITICAL" in line) for line in fh):
            reporter_handler.report("WARNING", "Errors logged in .stepup/director.log", [])

    sys.exit(returncode)


@attrs.define
class AsyncReadChar:
    old_tcattr: list | None = attrs.field(init=False, default=None)
    _loop = attrs.field(init=False)
    _thread: threading.Thread = attrs.field(init=False)
    _queue: asyncio.Queue = attrs.field(init=False, factory=asyncio.Queue)

    async def __aenter__(self):
        # Change stdin settings to read character by character.
        fd = sys.stdin.fileno()
        self.old_tcattr = termios.tcgetattr(fd)
        new_tcattr = termios.tcgetattr(fd)
        new_tcattr[3] &= ~(termios.ICANON | termios.ECHO)
        termios.tcsetattr(fd, termios.TCSAFLUSH, new_tcattr)
        self._loop = asyncio.get_running_loop()
        self._thread = threading.Thread(target=self._stdio_loop, daemon=True)
        self._thread.start()
        return self

    async def __aexit__(self, exc_type, exc_value, tb):
        # Revert stdin settings
        termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, self.old_tcattr)

    async def __call__(self) -> str:
        """Get one character from stdin."""
        return await self._queue.get()

    def _stdio_loop(self) -> str:
        """Blocking read of single character at a time from sys.stdin.

        Note that asyncio.StreamReader is not used because it puts stdin (and stdout and stderr)
        in non-blocking mode, which is not compatible with print functions and rich.print.
        (This problem is only noticeable when printing large amounts of data.

        This method is intended to be running in a separate daemon thread.
        """
        while True:
            char = sys.stdin.read(1)
            self._loop.call_soon_threadsafe(self._queue.put_nowait, char)


KEY_STROKE_HELP = """
   r = run     q = shutdown     d = drain     j = join     g = graph
"""


async def keyboard(
    director_socket_path: Path,
    reporter_socket_path: Path,
    stop_event: asyncio.Event,
):
    async with (
        ReporterClient.socket(reporter_socket_path) as reporter,
        AsyncReadChar() as readchar,
    ):
        async for ch in stoppable_iterator(readchar, stop_event):
            if ch in "qjdrg":
                async with await AsyncRPCClient.socket(director_socket_path) as client:
                    if ch == "q":
                        await reporter("KEYBOARD", "Shutting down")
                        await client.call.shutdown()
                    elif ch == "j":
                        await reporter("KEYBOARD", "Waiting for all steps before shutdown.")
                        await client.call.join()
                    elif ch == "d":
                        await reporter("KEYBOARD", "Draining the scheduler.")
                        await client.call.drain()
                    elif ch == "r":
                        await reporter("KEYBOARD", "Restarting the runner.")
                        await client.call.run()
                    elif ch == "g":
                        await client.call.graph("graph")
                        await reporter("KEYBOARD", "Workflow graph written to graph.txt.")
            else:
                pages = [("Keys", KEY_STROKE_HELP)]
                await reporter("KEYBOARD", f"Unsupported key {ch}", pages)


def boot_subcommand(subparsers) -> callable:
    """Parse command-line arguments."""
    parser = subparsers.add_parser(
        "boot",
        help="Boot the StepUp terminal user interface and director process.",
    )
    parser.add_argument(
        "--num-workers",
        "-n",
        type=Decimal,
        default=Decimal("1.2"),
        help="Number of parallel workers. "
        "When given as a real number with digits after the comma, "
        "it is multiplied with the number of available cores. [default=%(default)s]",
    )
    parser.add_argument(
        "--show-perf",
        "-s",
        default=0,
        action="count",
        help="Show the performance info on each line. Repeat for more detailed info.",
    )
    parser.add_argument(
        "--explain-rerun",
        "-e",
        default=False,
        action="store_true",
        help="Explain for every step with recording info why it cannot be skipped.",
    )
    if WATCHER_AVAILABLE:
        parser.add_argument(
            "--watch",
            "-w",
            default=False,
            action="store_true",
            help="StepUp will watch for file changes after all runnable steps have been executed. "
            "By pressing r, it will rerun steps  that have become pending due to the file changes. "
            "(Only supported on Linux.)",
        )
        parser.add_argument(
            "--watch-first",
            "-W",
            default=False,
            action="store_true",
            help="Start the runner after observing the first file change in watch mode. "
            "This implies --watch. (Only supported on Linux.)",
        )
    parser.add_argument(
        "--no-clean",
        dest="do_clean",
        default=True,
        action="store_false",
        help="Do not remove outdated output files.",
    )
    parser.add_argument(
        "--no-progress",
        dest="progress",
        default=True,
        action="store_false",
        help="Do not report progress information in the terminal user interface. "
        "(This can be useful to simplify and reduce the output.)",
    )
    parser.add_argument(
        "--perf",
        default=None,
        nargs="?",
        const=500,
        help="Run the director under perf record, by default at a frequency of 500 Hz.",
    )
    return boot_tool
