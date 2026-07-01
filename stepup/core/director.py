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
"""The director process manages the workflow and runs the steps as asyncio tasks."""

import argparse
import asyncio
import logging
import multiprocessing
import os
import signal
import sys
import time
import traceback
from decimal import Decimal
from importlib.metadata import version as get_version

import attrs
from path import Path

try:
    import yappi
except ImportError:
    yappi = None

from .asyncio import wait_for_events
from .builder import Builder
from .constants import DIRECTOR_LOG, DIRECTOR_PROF, GRAPH_DB
from .enums import HashUpdateCause, Need, ReturnCode, StepState
from .hash import FileHash
from .nglob import NGlobMulti
from .reporter import ReporterClient
from .rpc import allow_rpc, serve_socket_rpc
from .scheduler import Scheduler
from .sqlite3 import DBSession
from .startup import startup_from_db
from .step import Step
from .stepinfo import StepInfo
from .usage import PssSampler, format_resource_usage
from .watcher import WATCHER_AVAILABLE, Watcher
from .workflow import Workflow

__all__ = ("ServeResult", "get_socket", "interpret_jobs", "serve")


logger = logging.getLogger(__name__)


@attrs.define(frozen=True)
class ServeResult:
    """The outcome of `serve()`: the build's return code and its resource-usage summary."""

    returncode: ReturnCode = attrs.field()
    """The exit code of the director process."""

    resource_report: str = attrs.field()
    """A snapshot of CPU/IO/memory usage collected during this `serve()` call."""


def main():
    args = parse_args()
    mp_ctx = None
    if args.fork_runpy:
        mp_ctx = multiprocessing.get_context("forkserver")
        preload = ["stepup.core.executor", "stepup.core.hasher"]
        if args.preload_modules:
            preload.extend(m.strip() for m in args.preload_modules.split(",") if m.strip())
        mp_ctx.set_forkserver_preload(preload)
    with DBSession.open(GRAPH_DB) as db:
        asyncio.run(async_main(args, db, mp_ctx))


async def async_main(
    args: argparse.Namespace,
    db: DBSession,
    mp_ctx: multiprocessing.context.BaseContext | None = None,
):
    logging.basicConfig(
        format="%(asctime)s  %(levelname)8s  %(name)24s  ::  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        level=args.log_level,
    )
    print(f"SOCKET {args.director_socket}", file=sys.stderr)
    print(f"PID {os.getpid()}", file=sys.stderr)
    print(f"LOG_LEVEL {args.log_level}", file=sys.stderr)
    # To detect invalid usage of the RPCCLIENT in stepup.core.api  within the director process,
    # we set the STEPUP_DIRECTOR_SOCKET to an invalid value.
    os.environ["STEPUP_DIRECTOR_SOCKET"] = "_invalid_socket_for_director_process_"
    if args.yappi:
        if yappi is None:
            print(
                "Yappi profiling requested, but the yappi module is not installed.",
                file=sys.stderr,
            )
        else:
            yappi.set_clock_type("cpu")
            yappi.start(builtins=True, profile_threads=True)
    async with ReporterClient.socket(args.reporter_socket) as reporter:
        njob = interpret_jobs(args.jobs)
        await reporter.set_njob(njob)
        version = get_version("stepup")
        await reporter("DIRECTOR", f"Listening on {args.director_socket} (StepUp Core {version})")
        serve_result: ServeResult | None = None
        try:
            serve_result = await serve(
                director_socket_path=args.director_socket,
                njob=args.jobs,
                reporter=reporter,
                do_clean=args.clean,
                use_duration=args.duration,
                explain_rerun=args.explain_rerun,
                fix_epoch=args.fix_epoch,
                show_perf=args.show_perf,
                do_watch=args.watch,
                do_watch_first=args.watch_first,
                available_resources=args.resources,
                db=db,
                mp_ctx=mp_ctx,
            )
        except Exception as exc:
            tbstr = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            pages = [("Traceback", tbstr.strip())]
            await reporter("ERROR", "The director raised an exception.", pages)
            raise
        finally:
            await reporter("DIRECTOR", "See you!")
            await reporter.shutdown()
            if args.yappi and yappi is not None:
                yappi.stop()
                stats = yappi.get_func_stats()
                stats.save(DIRECTOR_PROF, type="pstat")
            if serve_result is not None:
                print(serve_result.resource_report, file=sys.stderr)
        sys.exit(serve_result.returncode.value)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="stepup-director",
        description="Launch the director.",
    )
    parser.add_argument(
        "director_socket",
        type=Path,
        help="The socket at which StepUp will listen for instructions.",
    )
    parser.add_argument(
        "--clean",
        dest="clean",
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
        default=False,
        action=argparse.BooleanOptionalAction,
        help="Explain for every step with recording info why it cannot be skipped.",
    )
    parser.add_argument(
        "--log-level",
        default="WARNING",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Set the logging level. [default=%(default)s]",
    )
    parser.add_argument(
        "--jobs",
        type=Decimal,
        default=Decimal("1.2"),
        help="Number of jobs running in parallel. "
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
        "--fork-runpy",
        default=False,
        action=argparse.BooleanOptionalAction,
        help="Use a forkserver for Python step execution and file hashing "
        "to reduce startup overhead. [default=%(default)s]",
    )
    parser.add_argument(
        "--preload-modules",
        default=None,
        help="Comma-separated list of Python modules to pre-load into the forkserver. "
        "Only has effect when --fork-runpy is active.",
    )
    parser.add_argument(
        "--reporter",
        type=Path,
        dest="reporter_socket",
        default=os.environ.get("STEPUP_REPORTER_SOCKET"),
        help="Socket to send reporter updates to, if any.",
    )
    parser.add_argument(
        "--show-perf",
        default=False,
        action=argparse.BooleanOptionalAction,
        help="Add performance details after completed step.",
    )
    parser.add_argument(
        "--resources",
        default=None,
        help="Available resources for steps, e.g. 'cpu:4,gpu:1,memgb:16'.",
    )
    if WATCHER_AVAILABLE:
        parser.add_argument(
            "--watch",
            default=False,
            action=argparse.BooleanOptionalAction,
            help="Watch file changes after completing the build phase. "
            "When not given, the director exists after completing the build phase.",
        )
        parser.add_argument(
            "--watch-first",
            default=False,
            action=argparse.BooleanOptionalAction,
            help="Exit watch phase and start the builder after the first file change. "
            "This implies --watch.",
        )
    parser.add_argument(
        "--yappi",
        default=False,
        action=argparse.BooleanOptionalAction,
        help="Profile the director with Yappi (must be installed).",
    )
    args = parser.parse_args()
    if WATCHER_AVAILABLE:
        if args.watch_first:
            args.watch = True
    else:
        args.watch = False
        args.watch_first = False
    return args


def interpret_jobs(jobs: Decimal) -> int:
    """Convert the command-line argument jobs into an integer."""
    if jobs.as_tuple().exponent < 0:
        if hasattr(os, "sched_getaffinity"):
            return int(len(os.sched_getaffinity(0)) * jobs)
        return int(os.cpu_count() * jobs)
    return int(jobs)


async def serve(
    *,
    director_socket_path: Path,
    njob: int,
    reporter: ReporterClient,
    do_clean: bool,
    use_duration: bool,
    explain_rerun: bool,
    fix_epoch: bool,
    show_perf: bool,
    do_watch: bool,
    do_watch_first: bool,
    available_resources: str | None,
    db: DBSession,
    mp_ctx=None,
) -> ServeResult:
    """Server program.

    Parameters
    ----------
    director_socket_path
        The socket to listen to for remote calls.
    njob
        The maximum number of steps to run concurrently.
    reporter
        The reporter client for sending information back to
        the terminal user interface.
    do_clean
        If True, the director removes outdated output files.
    use_duration
        If True, the scheduler uses the duration of steps to optimize the execution order.
    explain_rerun
        Report detailed diagnostics explaining why a step is rerun rather than skipped.
    show_perf
        Show performance details after each completed step.
    do_watch
        If True, the director alternates between build and watch phases until
        it receives an RPC to shutdown.
        If False, the director exits after a single build phase.
    do_watch_first
        If True, the builder restarts after the watcher sees the first file change.
    available_resources
        A dictionary of named resources and their available quantities,
        e.g. `{"cpu": 4, "gpu": 1}`. Defaults to an empty dict.
    mp_ctx
        A `multiprocessing` forkserver context for Python step execution and file hashing,
        or `None` to use plain subprocesses.

    Returns
    -------
    result
        The exit code of the director process, together with a resource-usage summary
        collected over the lifetime of this call (wall time, CPU time, block-IO op counts,
        and peak memory for the director and its step/hash child processes).
    """
    time_start = time.perf_counter()
    if njob < 1:
        raise ValueError(f"Number of parallel tasks must be strictly positive, got {njob}")
    if do_watch_first and not do_watch:
        raise ValueError("do_watch_first cannot be set without do_watch.")
    _check_plan("plan.py")

    # Environment variables exported to step child processes (and forkserver children).
    # These are passed explicitly to the executor rather than set in `os.environ`,
    # so that running the director in-process (e.g. in the test suite) does not
    # pollute the calling process's environment.
    infra_env = {
        "STEPUP_DIRECTOR_SOCKET": str(director_socket_path),
        "STEPUP_ROOT": str(Path.cwd()),
        "STEPUP_LOG_LEVEL": logging.getLevelName(logging.root.level),
    }
    if fix_epoch and "SOURCE_DATE_EPOCH" not in os.environ:
        infra_env["SOURCE_DATE_EPOCH"] = "315532800"

    # Create basic components
    dir_queue = asyncio.Queue() if do_watch else None
    workflow = Workflow(db, dir_queue=dir_queue)
    await workflow.initialize()
    scheduler = Scheduler(workflow, db=db, use_duration=use_duration)
    if available_resources is not None:
        await reporter("DIRECTOR", f"Setting available resources: {available_resources}")
    await scheduler.set_available_resources(available_resources)
    watcher = Watcher(workflow, db, reporter, dir_queue) if do_watch else None
    builder = Builder(
        njob=njob,
        watcher=watcher,
        scheduler=scheduler,
        workflow=workflow,
        db=db,
        reporter=reporter,
        show_perf=show_perf,
        explain_rerun=explain_rerun,
        do_remove_outdated=do_clean,
        mp_ctx=mp_ctx,
        infra_env=infra_env,
    )
    pss_sampler = PssSampler()
    stop_event = asyncio.Event()
    director_handler = DirectorHandler(
        scheduler, workflow, db, reporter, builder, watcher, stop_event
    )

    # Initialize the workflow
    new_boot = await director_handler.initialize_boot()
    if new_boot:
        await reporter("STARTUP", "(Re)initialized boot script")
        builder.resume.set()
    else:
        await startup_from_db(workflow, db, reporter, builder)

    # Start tasks and wait for them to complete
    exit_event = asyncio.Event()
    rpc_server = asyncio.create_task(
        serve_socket_rpc(director_handler, director_socket_path, exit_event)
    )
    coroutines = [
        builder.loop(stop_event),
        db.database_maintenance_loop(stop_event),
        pss_sampler.loop(stop_event),
    ]
    if watcher is not None:
        coroutines.append(watcher.loop(stop_event))
        if do_watch_first:
            coroutines.append(watch_first_loop(watcher, director_handler, stop_event))
    try:
        await asyncio.gather(*coroutines)
    finally:
        # In case of an exception, set the stop event, so other parts know they can stop waiting.
        stop_event.set()
        # Regular shutdown
        await builder.stop()
        exit_event.set()
        await rpc_server
        director_socket_path.remove_p()

    return ServeResult(
        returncode=builder.returncode,
        resource_report=format_resource_usage(
            time_start,
            builder.executor.step_accumulator,
            builder.executor.hash_accumulator,
            pss_sampler,
        ),
    )


def _check_plan(path_plan: str):
    """Basic sanity checks for a plan.py file."""
    if not Path(path_plan).is_file():
        raise ValueError(f"Is not a file: {path_plan}")
    if not os.access(path_plan, os.X_OK):
        raise ValueError(f"File is not executable: {path_plan}")
    with open(path_plan) as fh:
        shebang = "#!/usr/bin/env python3"
        if not fh.readline().rstrip() == shebang:
            raise ValueError(f"First line of plan differs from '{shebang}': {path_plan}")


@attrs.define
class DirectorHandler:
    scheduler: Scheduler = attrs.field()
    workflow: Workflow = attrs.field()
    db: DBSession = attrs.field()
    reporter: ReporterClient = attrs.field()
    builder: Builder = attrs.field()
    watcher: Watcher | None = attrs.field()
    stop_event: asyncio.Event = attrs.field()
    _shutdown_counter: int = attrs.field(init=False, default=0)

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
        async with self.db:
            return self.workflow.initialize_boot()

    @allow_rpc
    async def declare_missing(self, creator_i: int, paths: list[str]) -> list[tuple[str, FileHash]]:
        """Add a list of absolute paths to the workflow, to become static.

        They are stored internally as paths relative to STEPUP_ROOT, initially set to misssing.
        A list of available (cached) paths with file hashes is returned,
        which need to be updated on the client-side.
        The client then calls confirm with the updated hashes.

        The motivation for this two-step process is to avoid unnecessary file hash calculations.
        If the file size, inode number and modification time of a path match have not changed,
        we can reasonably safely assume that the file contents have not changed.
        In this case, the hash calculation is skipped and the old hash is reused.
        """
        async with self.db:
            creator = self.workflow.node(Step, creator_i)
            return self.workflow.declare_missing(creator, paths)

    @allow_rpc
    async def nglob(
        self, creator_i: int, patterns: list[str], subs: dict[str, str], paths: list[str]
    ):
        """Register a glob patterns to be watched."""
        ngm = NGlobMulti.from_patterns(patterns, subs)
        ngm.extend(paths)
        async with self.db:
            creator = self.workflow.node(Step, creator_i)
            self.workflow.register_nglob(creator, ngm)

    @allow_rpc
    async def static_trees(self, creator_i: int, paths: list[str]) -> list[tuple[str, FileHash]]:
        """Register directories whose contents become static files when used.

        Returns
        -------
        to_check
            A list of (path, file_hash) tuples to check and make static if valid.
        """
        to_check = []
        async with self.db:
            creator = self.workflow.node(Step, creator_i)
            for path in paths:
                to_check.extend(self.workflow.register_static_tree(creator, path))
        return to_check

    @allow_rpc
    async def confirm_hashes(self, checked: list[tuple[str, FileHash]]):
        """Mark missing files as static with up-to-date file hashes.

        Parameters
        ----------
        checked
            A list of (path, file_hash) tuples that have been updated and confirmed
            on the client side.
        """
        async with self.db:
            self.workflow.update_file_hashes(checked, HashUpdateCause.CONFIRMED)

    @allow_rpc
    async def step(
        self,
        creator_i: int,
        command: str,
        inp_paths: list[str],
        env_deps: list[str],
        out_paths: list[str],
        vol_paths: list[str],
        workdir: str,
        need: int,
        resources: dict[str, int],
        subshell: bool = False,
        env_overrides: dict[str, str] | None = None,
    ) -> list[tuple[str, FileHash]]:
        """Create a step in the workflow.

        Notes
        -----
        This is an RPC wrapper for `Workflow.define_step`.

        Returns
        -------
        to_check
            A list of (path, file_hash) tuples to check and make static if valid.
        """
        async with self.db:
            creator = self.workflow.node(Step, creator_i)
            return self.workflow.define_step(
                creator,
                command,
                inp_paths=inp_paths,
                env_deps=env_deps,
                out_paths=out_paths,
                vol_paths=vol_paths,
                workdir=workdir,
                need=Need(need),
                resources=resources,
                subshell=subshell,
                env_overrides=env_overrides,
            )

    @allow_rpc
    async def amend(
        self,
        step_i: int,
        inp_paths: list[str],
        env_deps: set[str],
        out_paths: list[str],
        vol_paths: list[str],
    ) -> tuple[bool, list[tuple[str, FileHash]]]:
        """Amend a step.

        Notes
        -----
        This is an RPC wrapper for `Workflow.amend_step`.

        Returns
        -------
        keep_going
            Whether the step is still runnable after amending.
        to_check
            A list of `(path, file_hash)` tuples to check and make static if valid.
            This is only relevant when `keep_going` is `True`.
            If some of the static tree matches cannot be confirmed,
            the caller has to change `keep_going` to `False`.
        """
        async with self.db:
            step = self.workflow.node(Step, step_i)
            return self.workflow.amend_step(
                step,
                inp_paths=inp_paths,
                env_deps=env_deps,
                out_paths=out_paths,
                vol_paths=vol_paths,
            )

    @allow_rpc
    async def reschedule_step(self, step_i: int, reason: str):
        """Reschedule a step for the given reason."""
        async with self.db:
            step = self.workflow.node(Step, step_i)
            step.add_rescheduled_info(reason)

    @allow_rpc
    async def record_subprocess(
        self,
        step_i: int,
        cmd: str,
        workdir: str,
        env_overrides: dict[str, str] | None,
        returncode: int,
        shell: bool,
        stdin: str,
        stdout: str,
        stderr: str,
    ):
        """Record a subprocess invocation made by a wrapper step.

        Notes
        -----
        This is an RPC wrapper for `Step.record_subprocess`.
        The recorded metadata is informative for archival and debugging, not authoritative.
        """
        async with self.db:
            step = self.workflow.node(Step, step_i)
            step.record_subprocess(
                cmd,
                workdir,
                env_overrides,
                returncode,
                shell,
                stdin,
                stdout,
                stderr,
            )

    @allow_rpc
    async def getinfo(self, step_i: int) -> StepInfo:
        """Return step information, consistent with return values of functions in stepup.core.api.

        For the sake of consistency, amended step arguments are not included.
        """
        async with self.db:
            step = self.workflow.node(Step, step_i)
            return step.get_step_info()

    #
    # Interactive use
    #

    @allow_rpc
    async def shutdown(self):
        """Shut down the director and stop all running tasks."""
        self.scheduler.on_hold = True
        if self.stop_event.is_set():
            signal_name, signal_number = (
                ("SIGINT", signal.SIGINT)
                if self._shutdown_counter == 1
                else ("SIGTERM", signal.SIGTERM)
            )
            await self.reporter("DIRECTOR", f"Interrupting running steps ({signal_name}).")
            await self.builder.interrupt_tasks(signal_number)
            self._shutdown_counter += 2
        else:
            if len(self.builder.running_tasks) > 0:
                await self.reporter("DIRECTOR", "Waiting for steps to complete before shutdown.")
            self.stop_event.set()
            self._shutdown_counter = 1
        if self.watcher is not None:
            self.watcher.interrupt.set()

    @allow_rpc
    async def drain(self):
        """Do not start new steps and switch to the watch phase after the build phase completes.

        Notes
        -----
        This RPC blocks until all running steps have completed.
        """
        self.scheduler.on_hold = True
        if self.watcher is not None:
            await wait_for_events(
                self.watcher.active, self.stop_event, return_when=asyncio.FIRST_COMPLETED
            )

    @allow_rpc
    async def join(self):
        """Block until the builder completed all (runnable) steps and shut down."""
        if self.watcher is not None:
            await wait_for_events(
                self.watcher.active, self.stop_event, return_when=asyncio.FIRST_COMPLETED
            )
            await self.shutdown()

    @allow_rpc
    async def graph(self, prefix: str):
        """Write out the graph in text format."""
        async with self.db:
            with open(f"{prefix}.txt", "w") as fh:
                print(self.workflow.format_str(), file=fh)
            with open(f"{prefix}_provenance.dot", "w") as fh:
                print(self.workflow.format_dot_provenance(), file=fh)
            with open(f"{prefix}_dependency.dot", "w") as fh:
                print(self.workflow.format_dot_dependency(), file=fh)

    @allow_rpc
    async def status(self) -> dict[str]:
        """Return the status of the director.

        Returns
        -------
        status
            A dictionary with the number of steps in each state and the running steps"""
        async with self.db:
            return {
                "step_counts": self.workflow.get_step_counts(),
                "file_counts": self.workflow.get_file_counts(),
                "running_steps": (
                    [step.label for step in self.workflow.steps(StepState.RUNNING)]
                    + [step.label for step in self.workflow.steps(StepState.CHECKING)]
                ),
                "resource_counts": self.scheduler.get_resource_counts(),
            }

    @allow_rpc
    async def run(self):
        """Run pending steps (based on file changes observed in the watch phase).

        Notes
        -----
        This has no effect during the build phase.
        """
        if self.watcher is None or not self.watcher.active.is_set():
            return
        async with self.db:
            # Make all failed steps pending again for rerun
            for step in self.workflow.steps(StepState.FAILED):
                step.mark_pending()
        self.watcher.interrupt.set()
        await wait_for_events(
            self.watcher.processed, self.stop_event, return_when=asyncio.FIRST_COMPLETED
        )
        self.scheduler.on_hold = False
        self.builder.resume.set()

    @allow_rpc
    async def watch_update(self, path: str):
        """Block until the watcher observed an update of the file."""
        if self.watcher is None:
            return
        path = Path(path).normpath()
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
        path = Path(path).normpath()
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
        """Block until the builder completed all (runnable) steps."""
        if self.watcher is None:
            return
        await wait_for_events(
            self.watcher.active, self.stop_event, return_when=asyncio.FIRST_COMPLETED
        )


def get_socket() -> str:
    """Block until the director socket is known and return it."""
    stepup_root = Path(os.getenv("STEPUP_ROOT", "."))
    path_director_log = stepup_root / DIRECTOR_LOG
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
                        "StepUp not running?"
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
    """When a file of the watcher has changed, call the builder after 0.5 seconds delay."""
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
