# StepUp Core provides the basic framework for the StepUp build tool.
# Copyright (C) 2024 Toon Verstraelen
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
import subprocess
import sys
import tempfile
import termios
from decimal import Decimal

import attrs
from path import Path

from .asyncio import stoppable_iterator
from .director import interpret_num_workers
from .reporter import ReporterClient, ReporterHandler
from .rpc import AsyncRPCClient, serve_socket_rpc

__all__ = ()


def main():
    asyncio.run(async_main())


async def async_main():
    args = parse_args()
    if Path(args.root).absolute() != Path.cwd():
        print("Changing to", args.root)
        os.chdir(args.root)

    # Create dir
    dir_stepup = Path(".stepup")
    dir_logs = dir_stepup / "logs"
    dir_logs.rmtree_p()
    dir_logs.makedirs_p()

    with tempfile.TemporaryDirectory(prefix="stepup-") as dir_sockets:
        dir_sockets = Path(dir_sockets)

        # Create socket paths
        director_socket_path = dir_sockets / "director"
        reporter_socket_path = dir_sockets / "reporter"

        # Set up the reporter monitor
        stop_event = asyncio.Event()
        reporter_handler = ReporterHandler(args.show_perf > 0, stop_event)
        task_reporter = asyncio.create_task(
            serve_socket_rpc(reporter_handler, reporter_socket_path, stop_event),
            name="reporter-rpc",
        )
        tasks = [task_reporter]

        # Launch director as background process
        num_workers = interpret_num_workers(args.num_workers)
        argv = [
            "-m",
            "stepup.core.director",
            args.plan_py,
            director_socket_path,
            f"--num-workers={num_workers}",
            f"--reporter={reporter_socket_path}",
        ]
        if args.show_perf > 1:
            argv.append("--show-perf")
        if args.explain_rerun:
            argv.append("--explain-rerun")
        try:
            with open(".stepup/logs/director", "w") as log_file:
                process_director = await asyncio.create_subprocess_exec(
                    sys.executable,
                    *argv,
                    stdin=subprocess.DEVNULL,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                )
                # Instantiate keyboard interaction or work non-interactively
                if args.interactive:
                    if sys.stdin.isatty():
                        await wait_for_path(director_socket_path, stop_event)
                        task_keyboard = asyncio.create_task(
                            keyboard(director_socket_path, reporter_socket_path, stop_event),
                            name="keyboard",
                        )
                        tasks.append(task_keyboard)
                else:
                    await wait_for_path(director_socket_path, stop_event)
                    async with await AsyncRPCClient.socket(director_socket_path) as client:
                        await client.call.join()
                await process_director.wait()
            stop_event.set()
        finally:
            try:
                await asyncio.gather(*tasks)
            except ConnectionRefusedError:
                reporter_handler.report("ERROR", "Could not connect to director", [])


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

    async def __aenter__(self):
        # Change stdin settings to read character by character.
        fd = sys.stdin.fileno()
        self.old_tcattr = termios.tcgetattr(fd)
        new_tcattr = termios.tcgetattr(fd)
        new_tcattr[3] &= ~(termios.ICANON | termios.ECHO)
        termios.tcsetattr(fd, termios.TCSAFLUSH, new_tcattr)
        return self

    async def __aexit__(self, exc_type, exc_value, tb):
        # Revert stdin settings
        termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, self.old_tcattr)

    async def __call__(self) -> str:
        """Read one character from stdin, using blocking io in a separate thread."""
        return await asyncio.to_thread(self._read_char)

    def _read_char(self) -> str:
        """Blocking read of single character from sys.stdin.

        Note that asyncio.StreamReader is not used because it puts stdin (and stdout and stderr)
        in non-blocking mode, which is not compatible with print functions and rich.print.
        (This problem is only noticable when printing large amounts of data.
        """
        return sys.stdin.read(1)


KEY_STROKE_HELP = """
   q = shutdown       d = drain        j = join   g = graph
   f = from scratch   t = try replay   r = run
"""


async def keyboard(
    director_socket_path: Path,
    reporter_socket_path: Path,
    stop_event: asyncio.Event,
):
    async with (
        ReporterClient.socket(reporter_socket_path) as reporter,
        await AsyncRPCClient.socket(director_socket_path) as client,
        AsyncReadChar() as readchar,
    ):
        async for ch in stoppable_iterator(readchar, stop_event):
            if ch == "q":
                await reporter("KEYBOARD", "Waiting for workers to complete before shutdown.")
                await client.call.shutdown()
                break
            elif ch == "d":
                await reporter("KEYBOARD", "Draining the scheduler and waiting for workers.")
                await client.call.drain()
            elif ch == "j":
                await reporter("KEYBOARD", "Waiting for the runner to complete to shutdown.")
                await client.call.join()
                break
            elif ch == "r":
                await reporter("KEYBOARD", "Restarting the runner.")
                async with asyncio.timeout(5):
                    await client.call.run()
            elif ch == "g":
                async with asyncio.timeout(5):
                    await client.call.graph("graph")
                await reporter("KEYBOARD", "Workflow graph written to graph.txt.")
            elif ch == "f":
                await reporter("KEYBOARD", "Discarding hashes and running from scratch.")
                async with asyncio.timeout(5):
                    await client.call.from_scratch()
            elif ch == "t":
                await reporter("KEYBOARD", "All steps marked pending and trying to replay.")
                async with asyncio.timeout(5):
                    await client.call.try_replay()
            else:
                pages = [("Keys", KEY_STROKE_HELP)]
                await reporter("KEYBOARD", f"Unsupported key {ch}", pages)


def parse_args():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(prog="stepup", description="The StepUp build tool")
    parser.add_argument("plan_py", default="plan.py", help="Top-level build script", nargs="?")
    parser.add_argument(
        "--root",
        default=os.getenv("STEPUP_ROOT", Path.cwd()),
        help="Directory containing top-level plan.py [default=%(default)s]",
    )
    parser.add_argument(
        "--num-workers",
        "-w",
        type=Decimal,
        default=Decimal("1.2"),
        help="Number of workers running in parallel. "
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
    parser.add_argument(
        "--non-interactive",
        "-n",
        dest="interactive",
        default=True,
        action="store_false",
        help="Disable keyboard interaction and quit after runner completes.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()
