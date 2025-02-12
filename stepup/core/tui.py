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
import os
import signal
import subprocess
import sys
import tempfile
import termios
import threading
from decimal import Decimal
from importlib.metadata import version as get_version

import attrs
from path import Path

from .asyncio import stoppable_iterator
from .director import interpret_num_workers
from .reporter import ReporterClient, ReporterHandler
from .rpc import SocketSyncRPCClient, serve_socket_rpc
from .utils import string_to_bool

__all__ = ()


def main():
    asyncio.run(async_main())


async def async_main():
    args = parse_args()
    if args.root.absolute() != Path.cwd():
        print("Changing to", args.root)
        args.root.cd()

    # Sanity check before creating a subdirectory.
    if not args.plan_py.is_file():
        raise RuntimeError(f"File {args.plan_py} does not exist.")

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
    if Path(".stepup/graph.db-wal").exists():
        raise RuntimeError("Old director still running? .stepup/graph.db-wal still exists.")
    if Path(".stepup/graph.db-shm").exists():
        raise RuntimeError("Old director still running? .stepup/graph.db-shm still exists.")

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

        # Install signal handlers
        stop_event = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in signal.SIGINT, signal.SIGTERM:
            loop.add_signal_handler(sig, stop_event.set)

        # Set up the reporter monitor
        reporter_handler = ReporterHandler(args.show_perf > 0, stop_event)
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
                args.plan_py,
                director_socket_path,
                f"--num-workers={num_workers}",
                f"--reporter={reporter_socket_path}",
                f"--log-level={args.log_level}",
            ]
        )
        if args.show_perf > 1:
            argv.append("--show-perf")
        if args.explain_rerun:
            argv.append("--explain-rerun")
        if args.watch:
            argv.append("--watch")
        if args.watch_first:
            argv.append("--watch-first")
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


async def wait_for_path(path: Path, stop_event: asyncio.Event):
    """Wait until a path exists."""
    time = 0.0
    while not path.exists():
        if stop_event.is_set():
            break
        if time > 0:
            await asyncio.sleep(time)
        time += 0.1


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
        (This problem is only noticable when printing large amounts of data.

        This method is intended to be running in a separate deamon thread.
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
    quit_messages = [
        "Waiting for steps to complete before shutdown.",
        "Second call to shutdown: killing steps with SIGINT.",
        "Third call to shutdown: killing steps with SIGKILL.",
    ]
    async with (
        ReporterClient.socket(reporter_socket_path) as reporter,
        AsyncReadChar() as readchar,
    ):
        async for ch in stoppable_iterator(readchar, stop_event):
            if ch == "q" and len(quit_messages) > 0:
                await reporter("KEYBOARD", quit_messages.pop(0))
                with SocketSyncRPCClient(director_socket_path) as client:
                    client.call.shutdown()
            elif ch == "j":
                await reporter("KEYBOARD", "Waiting for the runner to complete before shutdown.")
                with SocketSyncRPCClient(director_socket_path) as client:
                    client.call.join()
            elif ch == "d":
                await reporter("KEYBOARD", "Draining the scheduler and waiting for workers.")
                with SocketSyncRPCClient(director_socket_path) as client:
                    client.call.drain()
            elif ch == "r":
                await reporter("KEYBOARD", "Restarting the runner.")
                with SocketSyncRPCClient(director_socket_path) as client:
                    client.call.run()
            elif ch == "g":
                with SocketSyncRPCClient(director_socket_path) as client:
                    client.call.graph("graph")
                await reporter("KEYBOARD", "Workflow graph written to graph.txt.")
            else:
                pages = [("Keys", KEY_STROKE_HELP)]
                await reporter("KEYBOARD", f"Unsupported key {ch}", pages)


def parse_args():
    """Parse command-line arguments."""
    version = get_version("stepup")
    parser = argparse.ArgumentParser(prog="stepup", description="The StepUp build tool")
    parser.add_argument(
        "plan_py", type=Path, default=Path("plan.py"), help="Top-level build script", nargs="?"
    )
    parser.add_argument(
        "--explain-rerun",
        "-e",
        default=False,
        action="store_true",
        help="Explain for every step with recording info why it cannot be skipped.",
    )
    debug = string_to_bool(os.getenv("STEPUP_DEBUG", "0"))
    parser.add_argument(
        "--log-level",
        "-l",
        default=os.getenv("STEPUP_LOG_LEVEL", "DEBUG" if debug else "WARNING").upper(),
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Set the logging level. [default=%(default)s]",
    )
    parser.add_argument(
        "--num-workers",
        "-n",
        type=Decimal,
        default=Decimal("1.2"),
        help="Number of workers running in parallel. "
        "When given as a real number with digits after the comma, "
        "it is multiplied with the number of available cores. [default=%(default)s]",
    )
    parser.add_argument(
        "--perf",
        default=None,
        nargs="?",
        const=500,
        help="Run the director under perf record, by default at a frequency of 500 Hz.",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(os.getenv("STEPUP_ROOT", os.getcwd())),
        help="Directory containing top-level plan.py [default=%(default)s]",
    )
    parser.add_argument(
        "--show-perf",
        "-s",
        default=0,
        action="count",
        help="Show the performance info on each line. Repeat for more detailed info.",
    )
    parser.add_argument("--version", "-V", action="version", version="%(prog)s " + version)
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

    args = parser.parse_args()
    if args.watch_first:
        args.watch = True
    return args


if __name__ == "__main__":
    main()
