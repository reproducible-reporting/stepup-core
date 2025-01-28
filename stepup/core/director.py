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
"""The director process manages the workflow and sends jobs to the worker processes."""

import argparse
import asyncio
import logging
import os
import signal
import sqlite3
import sys
import time
import traceback
from decimal import Decimal
from importlib.metadata import version as get_version

import attrs
from path import Path

from .asyncio import wait_for_events
from .enums import ReturnCode, StepState
from .exceptions import GraphError
from .hash import FileHash
from .nglob import NGlobMulti
from .reporter import ReporterClient
from .rpc import allow_rpc, serve_socket_rpc
from .runner import Runner
from .scheduler import Scheduler
from .startup import startup_from_db
from .stepinfo import StepInfo
from .utils import DBLock, check_plan, mynormpath
from .watcher import Watcher
from .workflow import Workflow

__all__ = ("get_socket", "interpret_num_workers", "serve")


logger = logging.getLogger(__name__)


def main():
    asyncio.run(async_main())


async def async_main():
    args = parse_args()
    logging.basicConfig(
        format="%(asctime)s  %(levelname)8s  %(name)s  ::  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        level=args.log_level,
    )
    print(f"SOCKET {args.director_socket}", file=sys.stderr)
    logger.info("PID %s", os.getpid())
    async with ReporterClient.socket(args.reporter_socket) as reporter:
        num_workers = interpret_num_workers(args.num_workers)
        await reporter.set_num_workers(num_workers)
        version = get_version("stepup")
        await reporter("DIRECTOR", f"Listening on {args.director_socket} (StepUp {version})")
        try:
            returncode = await serve(
                Path(args.director_socket),
                num_workers,
                args.plan,
                reporter,
                args.show_perf,
                args.explain_rerun,
                args.watch,
                args.watch_first,
            )
        except Exception as exc:
            tbstr = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            pages = [("Traceback", tbstr.strip())]
            await reporter("ERROR", "The director raised an exception.", pages)
            raise
        finally:
            await reporter("DIRECTOR", "See you!")
            await reporter.shutdown()
        sys.exit(returncode.value)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="stepup-director",
        description="Launch the director.",
    )
    parser.add_argument(
        "plan",
        default="plan.py",
        help="The top-level plan.py script (must be in current directory).",
    )
    parser.add_argument(
        "director_socket",
        help="The socket at which StepUp will listen for instructions.",
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
        "--reporter",
        "-r",
        dest="reporter_socket",
        default=os.environ.get("STEPUP_REPORTER_SOCKET"),
        help="Socket to send reporter updates to, if any.",
    )
    parser.add_argument(
        "--show-perf",
        "-s",
        default=False,
        action="store_true",
        help="Add performance details after completed step.",
    )
    parser.add_argument(
        "--explain-rerun",
        "-e",
        default=False,
        action="store_true",
        help="Explain for every step with recording info why it cannot be skipped.",
    )
    parser.add_argument(
        "--watch",
        "-w",
        default=False,
        action="store_true",
        help="Watch file changes after completing the run phase. "
        "When not given, the director exists after completing the run phase.",
    )
    parser.add_argument(
        "--watch-first",
        "-W",
        default=False,
        action="store_true",
        help="Exit watch phase and start the runner after the first file change. "
        "This implies --watch.",
    )
    parser.add_argument(
        "--log-level",
        "-l",
        default="WARNING",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Set the logging level. [default=%(default)s]",
    )
    args = parser.parse_args()
    if args.watch_first:
        args.watch = True
    return args


def interpret_num_workers(num_workers: Decimal) -> int:
    """Convert the command-line argument num-workers into an integer."""
    if num_workers.as_tuple().exponent < 0:
        return int(len(os.sched_getaffinity(0)) * num_workers)
    return int(num_workers)


async def serve(
    director_socket_path: Path,
    num_workers: int,
    path_plan: str,
    reporter: ReporterClient,
    show_perf: bool,
    explain_rerun: bool,
    do_watch: bool,
    do_watch_first: bool,
) -> ReturnCode:
    """Server program.

    Parameters
    ----------
    director_socket_path
        The socket to listen to for remote calls.
    num_workers
        The number of worker processes.
    path_plan
        The initial `plan.py` file.
    reporter
        The reporter client for sending information back to
        the terminal user interface.
    show_perf
        Show performance details after each completed step.
    explain_rerun
        Let workers explain why steps with recording info cannot be skipped.
    do_watch
        If True, the director alternates between run and watch phases until
        it receives an RPC to shutdown.
        If False, the director exits after a single run phase.
    do_watch_first
        If True, the runner restarts after the watcher sees the first file change.

    Returns
    -------
    returncode
        The exit code of the director process.
    """
    if num_workers < 1:
        raise ValueError(f"Number of workers must be strictly positive, got {num_workers}")
    if do_watch_first and not do_watch:
        raise ValueError("do_watch_first cannot be set without do_watch.")
    check_plan(path_plan)

    # Create basic components
    con = sqlite3.connect(".stepup/graph.db", cached_statements=1024)
    dblock = DBLock(con)
    workflow = Workflow(con)
    scheduler = Scheduler(workflow.job_queue, workflow.job_queue_changed)
    scheduler.num_workers = num_workers
    watcher = Watcher(workflow, dblock, reporter, workflow.dir_queue) if do_watch else None
    runner = Runner(
        watcher,
        scheduler,
        workflow,
        dblock,
        reporter,
        director_socket_path,
        show_perf,
        explain_rerun,
    )
    stop_event = asyncio.Event()
    director_handler = DirectorHandler(
        scheduler, workflow, dblock, runner, watcher, path_plan, stop_event
    )

    # Install signal handlers
    loop = asyncio.get_running_loop()
    for sig in signal.SIGINT, signal.SIGTERM:
        loop.add_signal_handler(sig, scheduler.drain)

    # Initialize the workflow
    new_boot = await director_handler.initialize_boot()
    if new_boot:
        await reporter("STARTUP", "(Re)initialized boot script")
        runner.resume.set()
    else:
        await startup_from_db(workflow, dblock, runner, reporter)

    # Start tasks and wait for them to complete
    exit_event = asyncio.Event()
    rpc_server = asyncio.create_task(
        serve_socket_rpc(director_handler, director_socket_path, exit_event)
    )
    coroutines = [runner.loop(stop_event)]
    if watcher is not None:
        coroutines.append(watcher.loop(stop_event))
        if do_watch_first:
            coroutines.append(watch_first_loop(watcher, director_handler, stop_event))
    try:
        await asyncio.gather(*coroutines)
    finally:
        # In case of an exception, set the stop event, so other parts know then can stop waiting.
        stop_event.set()
        # Regular shutdown
        await reporter("DIRECTOR", "Stopping workers.")
        await runner.stop_workers()
        exit_event.set()
        await rpc_server
        director_socket_path.remove_p()
    return runner.returncode


@attrs.define
class DirectorHandler:
    scheduler: Scheduler = attrs.field()
    workflow: Workflow = attrs.field()
    dblock: DBLock = attrs.field()
    runner: Runner = attrs.field()
    watcher: Watcher | None = attrs.field()
    path_plan: str = attrs.field()
    stop_event: asyncio.Event = attrs.field()
    _kill_signals: list[int] = attrs.field(default=[signal.SIGINT, signal.SIGKILL])

    #
    # Building the workflow
    #

    async def initialize_boot(self) -> bool:
        """Define the initial plan.py as static file and create a step for it.

        Returns
        -------
        initialized
            Whether the boot script was (re)initialized.
        """
        if Path(self.path_plan).absolute().parent != Path.cwd():
            raise ValueError("The plan script must be in the current directory.")
        async with self.dblock:
            return self.workflow.initialize_boot(self.path_plan)

    @allow_rpc
    async def missing(self, creator_key: str, paths: list[str]) -> list[tuple[str, FileHash]]:
        """Add a list of absolute paths to the workflow, to become static.

        They are stored internally as paths relative to STEPUP_ROOT, initially set to misssing.
        A list of available (cached) paths with file hashes is returned,
        which need to be updated on the client-side.
        The client then calls confirm with the updated hashes.
        """
        async with self.dblock:
            creator = self.workflow.find(*creator_key.split(":", maxsplit=1))
            return self.workflow.declare_missing(creator, paths)

    @allow_rpc
    async def nglob(
        self, creator_key: str, patterns: list[str], subs: dict[str, str], paths: list[str]
    ):
        """Register a glob patterns to be watched."""
        ngm = NGlobMulti.from_patterns(patterns, subs)
        ngm.extend(paths)
        async with self.dblock:
            creator = self.workflow.find(*creator_key.split(":", maxsplit=1))
            self.workflow.register_nglob(creator, ngm)

    @allow_rpc
    async def defer(self, creator_key: str, patterns: list[str]) -> list[tuple[str, FileHash]]:
        """Register a deferred glob.

        Returns
        -------
        to_check
            A list of (path, file_hash) tuples to check and make static if valid.
        """
        to_check = []
        async with self.dblock:
            creator = self.workflow.find(*creator_key.split(":", maxsplit=1))
            for pattern in patterns:
                to_check.extend(self.workflow.defer_glob(creator, pattern))
        return to_check

    @allow_rpc
    async def filter_deferred(self, paths: list[str]) -> list[str]:
        """Test all paths against deferred globs and return matches.

        Returns
        -------
        to_check
            A list of (path, file_hash) tuples to check and make static if valid.
        """
        async with self.dblock:
            return self.workflow.filter_deferred(paths)

    @allow_rpc
    async def confirm(self, checked: list[tuple[str, FileHash]]):
        """Mark missing files as static because they were found to be present.

        Parameters
        ----------
        checked
            A list of (path, file_hash) tuples that have been updated and confirmed
            on the client side.
        """
        async with self.dblock:
            self.workflow.confirm_static(checked)

    @allow_rpc
    async def step(
        self,
        creator_key: str,
        command: str,
        inp_paths: list[str],
        env_vars: list[str],
        out_paths: list[str],
        vol_paths: list[str],
        workdir: str,
        optional: bool,
        pool: str | None,
        block: bool,
    ):
        """Create a step in the workflow.

        Notes
        -----
        This is an RPC wrapper for `Workflow.define_step`.
        """
        if not workdir.endswith(os.sep):
            raise GraphError(f"A working directory must end with a separator, got: {workdir}")
        async with self.dblock:
            creator = self.workflow.find(*creator_key.split(":", maxsplit=1))
            self.workflow.define_step(
                creator,
                command,
                inp_paths=inp_paths,
                env_vars=env_vars,
                out_paths=out_paths,
                vol_paths=vol_paths,
                workdir=workdir,
                optional=optional,
                pool=pool,
                block=block,
            )

    @allow_rpc
    async def pool(self, step_key: str, name: str, size: int):
        """Define a pool with given name and size.

        Notes
        -----
        This is an RPC wrapper for `Scheduler.define_pool`.
        """
        async with self.dblock:
            step = self.workflow.find(*step_key.split(":", maxsplit=1))
            self.workflow.define_pool(step, name, size)

    @allow_rpc
    async def amend(
        self,
        step_key: str,
        inp_paths: list[str],
        env_vars: set[str],
        out_paths: list[str],
        vol_paths: list[str],
    ) -> bool:
        """Amend a step.

        Notes
        -----
        This is an RPC wrapper for `Workflow.amend_step`.
        """
        async with self.dblock:
            step = self.workflow.find(*step_key.split(":", maxsplit=1))
            return self.workflow.amend_step(
                step,
                inp_paths=inp_paths,
                env_vars=env_vars,
                out_paths=out_paths,
                vol_paths=vol_paths,
            )

    @allow_rpc
    async def getinfo(self, step_key: str) -> StepInfo:
        """Return step information, consisent with return values of functions in stepup.core.api.

        For the sake of consistency, amended step arguments are not included.
        """
        async with self.dblock:
            step = self.workflow.find(*step_key.split(":", maxsplit=1))
            return step.get_step_info()

    #
    # Interactive use
    #

    @allow_rpc
    async def shutdown(self):
        """Shut down the director and worker processes."""
        self.scheduler.drain()
        if self.stop_event.is_set():
            if len(self._kill_signals) > 0:
                await self.runner.kill_worker_procs(self._kill_signals.pop(0))
        else:
            self.stop_event.set()
        if self.watcher is not None:
            self.watcher.interrupt.set()

    @allow_rpc
    async def drain(self):
        """Do not start new steps and switch to the watch phase after the steps completed.

        Notes
        -----
        This RPC blocks until all running steps have completed.
        """
        self.scheduler.drain()
        if self.watcher is not None:
            await wait_for_events(
                self.watcher.active, self.stop_event, return_when=asyncio.FIRST_COMPLETED
            )

    @allow_rpc
    async def join(self):
        """Block until the runner completed all (runnable) steps and shut down."""
        if self.watcher is not None:
            await wait_for_events(
                self.watcher.active, self.stop_event, return_when=asyncio.FIRST_COMPLETED
            )
            await self.shutdown()

    @allow_rpc
    async def graph(self, prefix: str):
        """Write out the graph in text format."""
        async with self.dblock:
            with open(f"{prefix}.txt", "w") as fh:
                print(self.workflow.format_str(), file=fh)
            with open(f"{prefix}_provenance.dot", "w") as fh:
                print(self.workflow.format_dot_provenance(), file=fh)
            with open(f"{prefix}_dependency.dot", "w") as fh:
                print(self.workflow.format_dot_dependency(), file=fh)

    @allow_rpc
    async def run(self):
        """Run pending steps (based on file changes observed in the watch phase).

        Notes
        -----
        This has no effect during the run phase.
        """
        if self.watcher is None or not self.watcher.active.is_set():
            return
        async with self.dblock:
            for step in self.workflow.steps(StepState.FAILED):
                step.mark_pending()
        self.watcher.interrupt.set()
        await wait_for_events(
            self.watcher.processed, self.stop_event, return_when=asyncio.FIRST_COMPLETED
        )
        self.scheduler.resume()
        self.runner.resume.set()

    @allow_rpc
    async def watch_update(self, path: str):
        """Block until the watcher observed an update of the file."""
        if self.watcher is None:
            return
        path = mynormpath(path)
        await wait_for_events(
            self.watcher.active, self.stop_event, return_when=asyncio.FIRST_COMPLETED
        )
        event = asyncio.Event()
        self.watcher.files_changed_events.add(event)
        try:
            while True:
                if path in self.watcher.updated:
                    return
                await event.wait()
                event.clear()
        finally:
            self.watcher.files_changed_events.discard(event)

    @allow_rpc
    async def watch_delete(self, path: str):
        """Block until the watcher observed the deletion of the file."""
        if self.watcher is None:
            return
        path = mynormpath(path)
        await wait_for_events(
            self.watcher.active, self.stop_event, return_when=asyncio.FIRST_COMPLETED
        )
        event = asyncio.Event()
        self.watcher.files_changed_events.add(event)
        try:
            while True:
                if path in self.watcher.deleted:
                    return
                await event.wait()
                event.clear()
        finally:
            self.watcher.files_changed_events.discard(event)

    @allow_rpc
    async def wait(self):
        """Block until the runner completed all (runnable) steps."""
        if self.watcher is None:
            return
        await wait_for_events(
            self.watcher.active, self.stop_event, return_when=asyncio.FIRST_COMPLETED
        )


def get_socket() -> str:
    """Block until the director socket is known and return it."""
    stepup_root = Path(os.getenv("STEPUP_ROOT", "./"))
    path_director_log = stepup_root / ".stepup/director.log"
    secs = 0
    while True:
        time.sleep(secs)
        if os.path.isfile(path_director_log):
            with open(path_director_log) as fh:
                line = fh.readline()
                if line.startswith("SOCKET"):
                    path_socket = Path(line[6:].strip())
                    if len(path_socket) > 2 and path_socket.exists():
                        return path_socket
                    message = (
                        f"Socket {path_socket} read from {path_director_log} does not exist. "
                        "Stepup not running?"
                    )
                else:
                    message = f"File {path_director_log} does not start with SOCKET line."
        else:
            message = f"File {path_director_log} not found."
        if secs == 0:
            print("Trying to contact StepUp director process.", file=sys.stderr)
        secs += 0.1
        print(f"{message}  Waiting {secs:.1f} seconds.", file=sys.stderr)


async def watch_first_loop(watcher: Watcher, director: DirectorHandler, stop_event: asyncio.Event):
    """When a file of the watcher has changed, call the runner after 0.5 seconds delay."""
    changed_event = asyncio.Event()
    watcher.files_changed_events.add(changed_event)
    while True:
        await watcher.active.wait()
        await wait_for_events(changed_event, stop_event, return_when=asyncio.FIRST_COMPLETED)
        if stop_event.is_set():
            break
        await asyncio.sleep(0.5)
        await director.run()


if __name__ == "__main__":
    main()
