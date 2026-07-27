# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""The director process manages the workflow and runs the steps as asyncio tasks."""

import argparse
import asyncio
import contextlib
import logging
import multiprocessing
import os
import signal
import sys
import time
import traceback
from collections.abc import Mapping
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
from .cgroups import get_ncore_from_cgroup
from .constants import DIRECTOR_LOG, DIRECTOR_PROF, GRAPH_DB, JOBLOG_CSV, SQLLOG_CSV, SQLLOG_JSON
from .enums import FileState, HashUpdateCause, Need, ReturnCode, StepState
from .exceptions import CgroupError, GraphError
from .executor import Executor
from .file import File
from .hash import FileHash
from .nglob import NGlobMulti
from .reporter import ReporterClient
from .rpc import allow_rpc, serve_socket_rpc
from .scheduler import Scheduler
from .sqlite3 import DBSession
from .startup import startup_from_db
from .stepinfo import StepInfo
from .usage import CgroupMemorySampler, format_resource_usage
from .watcher import WATCHER_AVAILABLE, Watcher
from .workflow import Workflow

__all__ = ("ServeResult", "get_ncore", "get_socket", "interpret_jobs", "serve")


logger = logging.getLogger(__name__)


INTERRUPT_GRACE = 5.0
"""Seconds a step gets to wind down after a terminal signal, before it is killed.

See `DirectorHandler.interrupt`.
"""


@attrs.define(frozen=True)
class ServeResult:
    """The outcome of `serve()`: the build's return code and its resource-usage summary."""

    returncode: ReturnCode = attrs.field()
    """The exit code of the director process."""

    usage_report: str = attrs.field()
    """A snapshot of CPU/IO/memory usage collected during this `serve()` call."""

    usage_summary: str = attrs.field()
    """A one-line summary of the resource usage, for screen display."""


def main():
    args = parse_args()
    mp_ctx = None
    if args.forkserver:
        mp_ctx = multiprocessing.get_context("forkserver")
        preload = ["stepup.core.executor"]
        if args.preload_modules:
            preload.extend(m.strip() for m in args.preload_modules.split(",") if m.strip())
        mp_ctx.set_forkserver_preload(preload)
    with DBSession.open(
        GRAPH_DB,
        path_sqllog=SQLLOG_JSON if args.sqllog else None,
        path_sqlcsv=SQLLOG_CSV if args.sqllog else None,
    ) as db:
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
    # To detect invalid usage of RPC_CLIENT in stepup.core.api within the director process,
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
        serve_result = None
        try:
            serve_result = await serve(
                director_socket_path=args.director_socket,
                njob=njob,
                reporter=reporter,
                do_cgroup=args.cgroup,
                do_clean=args.clean,
                use_duration=args.duration,
                explain_rerun=args.explain_rerun,
                keep_going=args.keep_going,
                fix_epoch=args.fix_epoch,
                do_joblog=args.joblog,
                live_progress=args.live_progress,
                do_watch=args.watch,
                do_watch_first=args.watch_first,
                available_resources=args.resources,
                postpone_cap=args.postpone_cap,
                targets=args.targets,
                target_dirs=args.target_dirs,
                db=db,
                mp_ctx=mp_ctx,
            )
        except Exception as exc:
            tbstr = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            pages = [("Traceback", tbstr.strip())]
            await reporter("ERROR", "The director raised an exception.", pages)
            raise
        finally:
            if serve_result is not None and len(serve_result.usage_summary) > 0:
                await reporter("DIRECTOR", serve_result.usage_summary)
            await reporter("DIRECTOR", "See you!")
            await reporter.shutdown()
            if args.yappi and yappi is not None:
                yappi.stop()
                stats = yappi.get_func_stats()
                stats.save(DIRECTOR_PROF, type="pstat")
            if serve_result is not None:
                print(serve_result.usage_report, file=sys.stderr)
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
        "--cgroup",
        default=False,
        action=argparse.BooleanOptionalAction,
        help="Enable peak memory usage tracking through cgroups.",
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
        "--keep-going",
        default=False,
        action=argparse.BooleanOptionalAction,
        help="Keep dispatching new steps after another step has failed, "
        "instead of putting the scheduler on hold. (In-progress steps always finish.)",
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
        default=Decimal("1.0"),
        help="Number of jobs running in parallel. "
        "When given as a real number with digits after the decimal point, "
        "it is multiplied with the number of available cores. [default=%(default)s]",
    )
    parser.add_argument(
        "--joblog",
        default=False,
        action=argparse.BooleanOptionalAction,
        help=f"Record job-execution events (init, created, started, ended, completed) to "
        f"{JOBLOG_CSV}, for diagnosing scheduler/executor dispatch overhead.",
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
        default=False,
        action=argparse.BooleanOptionalAction,
        help="Use a forkserver for Python step execution and file hashing "
        "to reduce startup overhead. [default=%(default)s]",
    )
    parser.add_argument(
        "--preload-modules",
        default=None,
        help="Comma-separated list of Python modules to pre-load into the forkserver. "
        "Only has effect when --forkserver is active.",
    )
    parser.add_argument(
        "--live-progress",
        default=False,
        action=argparse.BooleanOptionalAction,
        help="Send live step-count updates to the reporter. Set by the TUI when its "
        "console is an interactive terminal. [default=%(default)s]",
    )
    parser.add_argument(
        "--reporter",
        type=Path,
        dest="reporter_socket",
        default=os.environ.get("STEPUP_REPORTER_SOCKET"),
        help="Socket to send reporter updates to, if any.",
    )
    parser.add_argument(
        "--postpone-cap",
        type=int,
        default=100,
        help="Maximum number of consecutive postpones (since the last success) before "
        "a step is failed instead of parked. A livelock guard. [default=%(default)s]",
    )
    parser.add_argument(
        "--resources",
        default=None,
        help="Available resources for steps, e.g. 'cpu:4,gpu:1,memgb:16'.",
    )
    parser.add_argument(
        "--sqllog",
        default=False,
        action=argparse.BooleanOptionalAction,
        help=f"Enable SQLite debug logging: append per-query timing rows to {SQLLOG_CSV} "
        f"as they execute, and write a query/call-site/plan index to {SQLLOG_JSON} "
        "when the director exits.",
    )
    parser.add_argument(
        "--target",
        dest="targets",
        action="append",
        default=[],
        type=Path,
        help="Restrict the build to steps needed to produce this output file. "
        "May be repeated. When omitted, the full default workflow is built.",
    )
    parser.add_argument(
        "--target-dir",
        dest="target_dirs",
        action="append",
        default=[],
        type=Path,
        help="Restrict the build to declared-DEFAULT steps whose output falls under this "
        "directory (trailing slash included). May be repeated. Director-internal: the "
        "TUI classifies raw CLI targets into --target/--target-dir; see tui.py.",
    )
    if WATCHER_AVAILABLE:
        parser.add_argument(
            "--watch",
            default=False,
            action=argparse.BooleanOptionalAction,
            help="Watch file changes after completing the build phase. "
            "When not given, the director exits after completing the build phase.",
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


# Environment variables through which batch schedulers advertise the number of cores
# allocated to the current job, in order of priority.
SCHEDULER_CPU_ENV_VARS = ("SLURM_CPUS_PER_TASK", "PBS_NUM_PPN", "NCPUS")


def get_ncore() -> int:
    """Determine the number of CPU cores available to this process.

    Cgroup v2 accounting (`cpuset.cpus.effective` / `cpu.max`) is tried first, since
    that reflects the containment actually applied to this process, e.g. by Slurm
    or PBS. Scheduler-provided environment variables are the fallback (they are
    only advisory and can disagree with actual cgroup containment), then the
    OS-reported core count, because a batch scheduler may allocate fewer cores
    than are physically present on the node.
    """
    try:
        return get_ncore_from_cgroup()
    except CgroupError:
        pass
    for var in SCHEDULER_CPU_ENV_VARS:
        value = os.environ.get(var)
        if value is not None:
            return int(value)
    if hasattr(os, "sched_getaffinity"):
        return len(os.sched_getaffinity(0))
    return os.cpu_count()


def interpret_jobs(jobs: Decimal) -> int:
    """Convert the command-line argument jobs into an integer."""
    ncore = get_ncore() if jobs.as_tuple().exponent < 0 else 1
    return int(ncore * jobs)


async def serve(
    *,
    director_socket_path: Path,
    njob: int,
    reporter: ReporterClient,
    do_cgroup: bool,
    do_clean: bool,
    use_duration: bool,
    explain_rerun: bool,
    keep_going: bool,
    fix_epoch: bool,
    do_joblog: bool,
    live_progress: bool,
    do_watch: bool,
    do_watch_first: bool,
    available_resources: str | None,
    postpone_cap: int,
    targets: list[Path],
    target_dirs: list[Path],
    db: DBSession,
    mp_ctx: multiprocessing.context.BaseContext | None = None,
    handle_signals: bool = True,
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
    do_cgroup
        If True, the director tracks peak memory usage through cgroups.
    do_clean
        If True, the director removes outdated output files.
    use_duration
        If True, the scheduler uses the duration of steps to optimize the execution order.
    explain_rerun
        Report detailed diagnostics explaining why a step is rerun rather than skipped.
    keep_going
        If True, keep dispatching new steps after another step has failed
        (like `make -k`). If False (default), the scheduler is put on hold after
        the first failure; steps already running are still allowed to finish.
    fix_epoch
        If True, set the `SOURCE_DATE_EPOCH` environment variable for step child
        processes (unless already set in the environment), for reproducible builds.
    do_joblog
        If True, record job-execution events (created, started, ended, completed) to
        `JOBLOG_CSV`, for diagnosing scheduler/executor dispatch overhead.
    live_progress
        Whether the reporter is an interactive terminal that wants live step-count updates.
    do_watch
        If True, the director alternates between build and watch phases until
        it receives an RPC to shutdown.
        If False, the director exits after a single build phase.
    do_watch_first
        If True, the builder restarts after the watcher sees the first file change.
    available_resources
        Named resources and their available quantities, e.g. `"cpu:4,gpu:1"`, or `None`
        to declare no resources at all (any step that requests a named resource then
        never becomes runnable).
    postpone_cap
        Maximum number of consecutive postpones (since a step last succeeded) before
        it is failed instead of parked pending again. A livelock guard.
    targets
        Restrict the build to steps needed to produce these output files.
        An empty list builds the full default workflow.
    target_dirs
        Restrict the build to declared-DEFAULT steps whose output falls under one of
        these directories.
    db
        The database session backing the workflow graph.
    mp_ctx
        A `multiprocessing` forkserver context for Python step execution and file hashing,
        or `None` to use plain subprocesses.
    handle_signals
        If True, install handlers for `SIGINT` and `SIGTERM` that abort the build,
        see `DirectorHandler.interrupt`.
        Set to False when running the director inside another process (e.g. the test suite),
        where hijacking the process-wide signal handlers is not wanted.

    Returns
    -------
    result
        The exit code of the director process, together with a resource-usage summary collected
        over the lifetime of this call:
        - wall time
        - CPU time (user/system)
        - peak memory for the director (and optionally its step child processes).
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
    workflow = Workflow(
        db,
        dir_queue=dir_queue,
        postpone_cap=postpone_cap,
        targets=targets,
        target_dirs=target_dirs,
    )
    await workflow.initialize()
    scheduler = Scheduler(workflow, db=db, use_duration=use_duration, do_joblog=do_joblog)
    if available_resources is not None:
        await reporter("DIRECTOR", f"Setting available resources: {available_resources}")
    await scheduler.initialize(available_resources)
    executor = Executor(
        scheduler=scheduler,
        workflow=workflow,
        db=db,
        reporter=reporter,
        mp_ctx=mp_ctx,
        explain_rerun=explain_rerun,
        keep_going=keep_going,
        live_progress=live_progress,
        do_joblog=do_joblog,
        infra_env=infra_env,
    )
    # Builder is agnostic of watch mode; it is built first because the watcher needs
    # builder.hash_queue, sharing the wake event that a hash-job submission uses to
    # nudge a parked job_loop.
    builder = Builder(
        njob=njob,
        scheduler=scheduler,
        workflow=workflow,
        db=db,
        reporter=reporter,
        live_progress=live_progress,
        do_remove_outdated=do_clean,
        executor=executor,
    )
    watcher = (
        Watcher(
            workflow,
            db,
            reporter,
            dir_queue,
            executor=executor,
            hash_queue=builder.hash_queue,
            njob=njob,
        )
        if do_watch
        else None
    )
    memory_sampler = CgroupMemorySampler() if do_cgroup else None
    stop_event = asyncio.Event()
    director_handler = DirectorHandler(
        scheduler, workflow, db, reporter, executor, builder, watcher, stop_event
    )

    # Initialize the workflow
    new_boot = await director_handler.initialize_boot()
    if new_boot:
        await reporter("STARTUP", "(Re)initialized boot script")
        builder.resume.set()
    else:
        await startup_from_db(workflow, db, reporter, builder)

    # Validate targets against the (re)loaded graph and flag affected steps for
    # recompute. Must run after the boot/resume block above: on a resumed database,
    # startup_from_db's file scan is what marks a changed plan.py's step PENDING, which
    # Workflow.reconcile_targets()'s creator-chain guard consults. Must run before the
    # task gather below, since the builder loop (and thus the first dispatch) only
    # starts there.
    async with db:
        try:
            workflow.reconcile_targets()
        except GraphError as exc:
            await reporter("ERROR", f"Invalid build target: {exc}")
            await reporter.check_logs()
            return ServeResult(returncode=ReturnCode.FAILED, usage_report="", usage_summary="")

    # Start tasks and wait for them to complete
    exit_event = asyncio.Event()
    rpc_server = asyncio.create_task(
        serve_socket_rpc(director_handler, director_socket_path, exit_event)
    )
    coroutines = [
        build_loop(builder, watcher, stop_event),
        db.database_maintenance_loop(stop_event),
    ]
    if memory_sampler is not None:
        coroutines.append(memory_sampler.loop(stop_event))
    if watcher is not None:
        coroutines.append(watcher.loop(stop_event))
        if do_watch_first:
            coroutines.append(watch_first_loop(watcher, director_handler, stop_event))
    # Abort the build on a terminal signal, instead of dying with a KeyboardInterrupt
    # traceback (SIGINT) or instantly and mid-transaction (SIGTERM).
    loop = asyncio.get_running_loop()
    if handle_signals:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, director_handler.interrupt, sig)
    try:
        await asyncio.gather(*coroutines)
    finally:
        # In case of an exception, set the stop event, so other parts know they can stop waiting.
        stop_event.set()
        # Regular shutdown. The signal handlers stay installed for its duration:
        # a step ignoring the first interrupt is killed by a second one during `builder.stop()`.
        await builder.stop()
        exit_event.set()
        await rpc_server
        director_socket_path.remove_p()
        await director_handler.close()
        if handle_signals:
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.remove_signal_handler(sig)

    usage_report, usage_summary = format_resource_usage(
        time_start,
        builder.executor.step_accumulator,
        memory_sampler,
    )

    return ServeResult(
        returncode=builder.returncode, usage_report=usage_report, usage_summary=usage_summary
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
    executor: Executor = attrs.field()
    builder: Builder = attrs.field()
    watcher: Watcher | None = attrs.field()
    stop_event: asyncio.Event = attrs.field()
    _shutdown_counter: int = attrs.field(init=False, default=0)

    _interrupt_count: int = attrs.field(init=False, default=0)
    """The number of terminal signals received so far, see `interrupt`."""

    _interrupt_task: asyncio.Task | None = attrs.field(init=False, default=None)
    """The in-flight `_interrupt` task, kept alive here so it cannot be garbage-collected."""

    _interrupt_escalate: asyncio.Event = attrs.field(init=False, factory=asyncio.Event)
    """Set by a second terminal signal, to cut the grace period of the first one short."""

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

    def _submit_to_check(self, to_check: Mapping[str, FileHash]) -> None:
        """Submit a hash job for each `path: old_hash` entry, applied with `cause=CONFIRMED`.

        Fire-and-forget: the caller does not wait for these jobs. Each job's own
        completion flips the file from `UNCONFIRMED` to `STATIC` or `MISSING` and wakes
        `job_loop`, which is what lets a step consuming the file become runnable.
        """
        for path, old_hash in to_check.items():
            self.builder.hash_queue.submit(path, old_hash, HashUpdateCause.CONFIRMED)

    @allow_rpc
    async def declare_unconfirmed(self, job_i: int, paths: list[str]) -> None:
        """Add a list of absolute paths to the workflow, to become static.

        They are stored internally as paths relative to `${STEPUP_ROOT}`,
        initially set to `UNCONFIRMED`.
        A hash job is submitted for each, running in parallel with other work,
        to confirm it as `STATIC` or `MISSING`.

        The `UNCONFIRMED` intermediate state avoids unnecessary file hash calculations.
        If the file size, inode number and modification time of a path match have not changed,
        we can reasonably safely assume that the file contents have not changed.
        In this case, the hash calculation is skipped and the old hash is reused.
        """
        async with self.db:
            creator = self.scheduler.get_step(job_i)
            to_check = self.workflow.declare_unconfirmed(creator, paths)
        self._submit_to_check(to_check)

    @allow_rpc
    async def static_trees(self, job_i: int, paths: list[str]) -> None:
        """Register directories whose contents become static files when used."""
        to_check = {}
        async with self.db:
            creator = self.scheduler.get_step(job_i)
            for path in paths:
                to_check.update(self.workflow.register_static_tree(creator, path))
        self._submit_to_check(to_check)

    @allow_rpc
    async def nglob(
        self, job_i: int, patterns: list[str], subs: dict[str, str], paths: list[str]
    ) -> None:
        """Register glob patterns to be watched."""
        ngm = NGlobMulti.from_patterns(patterns, subs)
        ngm.extend(paths)
        async with self.db:
            creator = self.scheduler.get_step(job_i)
            self.workflow.register_nglob(creator, ngm)

    @allow_rpc
    async def step(
        self,
        job_i: int,
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
        duration: float | None = None,
    ) -> None:
        """Create a step in the workflow.

        Notes
        -----
        This is an RPC wrapper for `Workflow.define_step`.
        """
        async with self.db:
            creator = self.scheduler.get_step(job_i)
            to_check = self.workflow.define_step(
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
                duration=duration,
            )
        self._submit_to_check(to_check)
        # The new step may already be runnable, but the builder's job loop may be parked,
        # waiting for a running task to finish.
        # Wake it up so it re-polls the scheduler instead of waiting for an unrelated task.
        self.builder.wake_job_loop.set()

    @allow_rpc
    async def hold(self, job_i: int) -> None:
        """Hold back this step's children from dispatch until a matching `release()`.

        Notes
        -----
        This is an RPC wrapper for `Step.hold`, which is re-entrant: nested `hold()` calls
        increment a counter, and children stay held back until the outermost `release()`.
        No job-loop wake-up is needed here: holding never creates new runnable work.
        """
        async with self.db:
            step = self.scheduler.get_step(job_i)
            step.hold()

    @allow_rpc
    async def release(self, job_i: int) -> None:
        """Release one `hold()` on this step, decrementing its open-hold counter.

        Notes
        -----
        This is an RPC wrapper for `Step.release`.
        """
        async with self.db:
            step = self.scheduler.get_step(job_i)
            step.release()
        # Previously held-back children may now be runnable, but the builder's job loop
        # may be parked, waiting for a running task to finish.
        # Wake it up so it re-polls the scheduler instead of waiting for an unrelated task.
        self.builder.wake_job_loop.set()

    @allow_rpc
    async def amend(
        self,
        job_i: int,
        inp_paths: list[str],
        env_deps: set[str],
        out_paths: list[str],
        vol_paths: list[str],
    ) -> bool:
        """Amend a step.

        Notes
        -----
        This is an RPC wrapper for `Workflow.amend_step`.

        When some amended inputs are still `UNCONFIRMED` (matches of a static tree not yet hashed),
        this call blocks until they are resolved to `STATIC` or `MISSING`,
        running their hash jobs immediately rather than through the builder's queue:
        the calling step already occupies a slot and is idle while it waits,
        so promoting its hash jobs outside the `--jobs` budget keeps real concurrency at `njob`,
        instead of deadlocking when every slot holds a step blocked here.

        Returns
        -------
        carry_on
            Whether the step is still runnable after amending.
        """
        async with self.db:
            step = self.scheduler.get_step(job_i)
            is_detached, unavailable, unfresh, to_check = self.workflow.amend_step(
                step,
                inp_paths=inp_paths,
                env_deps=env_deps,
                out_paths=out_paths,
                vol_paths=vol_paths,
                ran_concurrently=self.scheduler.ran_concurrently,
            )
        if to_check:
            checked_paths = set(to_check)
            await self.builder.run_promoted_hash_jobs(to_check, HashUpdateCause.CONFIRMED)
            async with self.db:
                is_detached = step.is_detached()
                if not is_detached:
                    for path in checked_paths:
                        file = self.workflow.find(File, path)
                        if file.get_state() not in (FileState.STATIC, FileState.BUILT):
                            unavailable.add(path)
        carry_on = len(unavailable) == 0 and len(unfresh) == 0
        if not carry_on:
            self.executor.postpone(job_i, unavailable=unavailable, unfresh=unfresh)
        if is_detached:
            carry_on = False
        return carry_on

    @allow_rpc
    async def postpone_step(self, job_i: int, missing: list[str]) -> None:
        """Postpone a step due to unavailable dependencies."""
        self.executor.postpone(job_i, unavailable=missing)

    @allow_rpc
    async def record_subprocess(
        self,
        job_i: int,
        cmd: str,
        workdir: str,
        env_overrides: dict[str, str] | None,
        returncode: int,
        shell: bool,
        stdin: str,
        stdout: str,
        stderr: str,
    ) -> None:
        """Record a subprocess invocation made by a wrapper step.

        Notes
        -----
        This is an RPC wrapper for `Step.record_subprocess`.
        The recorded metadata is informative for archival and debugging, not authoritative.
        """
        async with self.db:
            step = self.scheduler.get_step(job_i)
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
    async def getinfo(self, job_i: int) -> StepInfo:
        """Return step information, consistent with return values of functions in stepup.core.api.

        For the sake of consistency, amended step arguments are not included.
        """
        async with self.db:
            step = self.scheduler.get_step(job_i)
            return step.get_step_info()

    #
    # Interactive use
    #

    @allow_rpc
    async def shutdown(self) -> None:
        """Shut down the director, escalating if called repeatedly.

        The first call puts the scheduler on hold and stops the build/watch loops
        gracefully: steps already running are left to finish on their own.
        A second call interrupts running steps with `SIGINT`;
        a third and any further call escalates to `SIGKILL`.

        Waiting for running steps is the point of this (deliberate, interactive) route,
        so it escalates only when asked to, never on a timer.
        A terminal signal takes the more abrupt `interrupt` route instead.
        """
        self.scheduler.on_hold = True
        if self.stop_event.is_set():
            signal_name, signal_number = (
                ("SIGINT", signal.SIGINT)
                if self._shutdown_counter == 1
                else ("SIGKILL", signal.SIGKILL)
            )
            await self.reporter("DIRECTOR", f"Interrupting running steps ({signal_name}).")
            self.executor.interrupt(signal_number)
            self._shutdown_counter += 2
        else:
            if len(self.builder.running_tasks) > 0:
                await self.reporter("DIRECTOR", "Waiting for steps to complete before shutdown.")
            self.stop_event.set()
            self._shutdown_counter = 1
        if self.watcher is not None:
            self.watcher.interrupt.set()

    def interrupt(self, sig: signal.Signals) -> None:
        """Abort the build because a terminal signal was received.

        Unlike `shutdown` (the `q` key), this never waits for running steps:
        the user asked for everything to stop, so the build ends as soon as the steps do.
        The escalation to `SIGKILL` is on a timer (`INTERRUPT_GRACE`) rather than on further
        signals, so that a single Ctrl-C is always enough to get the shell prompt back.
        A second signal only cuts that grace period short.

        This is a plain callback (not a coroutine), as required by `add_signal_handler`.
        The work happens in a task, since reporting and waiting are asynchronous.
        """
        self._interrupt_count += 1
        if self._interrupt_count == 1:
            self._interrupt_task = asyncio.create_task(self._interrupt(sig), name="interrupt")
        else:
            self._interrupt_escalate.set()

    async def _interrupt(self, sig: signal.Signals) -> None:
        """Stop scheduling, interrupt running steps and kill whatever ignores that."""
        await self.reporter(
            "DIRECTOR", f"Aborting the build ({sig.name}). Interrupting running steps (SIGINT)."
        )
        self.scheduler.on_hold = True
        # Steps run in a session of their own, so the terminal does not signal them:
        # this is the only thing that stops them, on every route (Ctrl-C, SIGTERM, or a
        # SIGINT sent to the terminal user interface alone).
        self.executor.interrupt(signal.SIGINT)
        self.stop_event.set()
        if self.watcher is not None:
            self.watcher.interrupt.set()
        # Give the steps a moment to wind down on their own. Polling keeps this simple and
        # lets shutdown proceed immediately once the last step is gone, which is the common case.
        loop = asyncio.get_running_loop()
        deadline = loop.time() + INTERRUPT_GRACE
        while len(self.builder.running_tasks) > 0 and not self._interrupt_escalate.is_set():
            if loop.time() > deadline:
                break
            await asyncio.sleep(0.1)
        if len(self.builder.running_tasks) > 0:
            await self.reporter("DIRECTOR", "Killing unresponsive steps (SIGKILL).")
            self.executor.interrupt(signal.SIGKILL)

    async def close(self) -> None:
        """Cancel a pending `interrupt` grace period, e.g. when the build ended in time."""
        if self._interrupt_task is not None and not self._interrupt_task.done():
            self._interrupt_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._interrupt_task
        self._interrupt_task = None

    @allow_rpc
    async def drain(self) -> None:
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
    async def join(self) -> None:
        """Block until the builder completed all (runnable) steps and shut down."""
        if self.watcher is not None:
            await wait_for_events(
                self.watcher.active, self.stop_event, return_when=asyncio.FIRST_COMPLETED
            )
            await self.shutdown()

    @allow_rpc
    async def graph(self, prefix: str) -> None:
        """Write out the graph in text format."""
        async with self.db:
            with open(f"{prefix}.txt", "w") as fh:
                print(self.workflow.format_str(), file=fh)
            with open(f"{prefix}_provenance.dot", "w") as fh:
                print(self.workflow.format_dot_provenance(), file=fh)
            with open(f"{prefix}_dependency.dot", "w") as fh:
                print(self.workflow.format_dot_dependency(), file=fh)

    @allow_rpc
    async def run(self) -> None:
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
                self.workflow.mark_step_pending(step)
        self.watcher.interrupt.set()
        await wait_for_events(
            self.watcher.processed, self.stop_event, return_when=asyncio.FIRST_COMPLETED
        )
        self.scheduler.on_hold = False
        self.builder.resume.set()

    @allow_rpc
    async def watch_update(self, path: str) -> None:
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
    async def watch_delete(self, path: str) -> None:
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
    async def wait(self) -> None:
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


async def build_loop(builder: Builder, watcher: Watcher | None, stop_event: asyncio.Event):
    """Repeatedly run build phases until `stop_event` fires.

    `Builder` itself has no notion of watch mode, so this is where that policy lives:
    after each phase, hand off to `watcher` to resume file-system monitoring, or,
    without a watcher, stop after a single phase.
    """
    while await builder.run_phase(stop_event):
        if watcher is None:
            stop_event.set()
        else:
            watcher.resume.set()


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
