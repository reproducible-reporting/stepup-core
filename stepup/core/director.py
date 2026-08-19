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
from .constants import DIRECTOR_PROF, GRAPH_DB, JOBLOG_CSV, PLAN_PY, SQLLOG_CSV, SQLLOG_JSON
from .enums import FileState, HashUpdateCause, Need, ReturnCode, StepState
from .exceptions import CgroupError, GraphError
from .executor import Executor
from .file import File
from .hash import FileHash
from .nglob import NamedGlob
from .reporter import ReporterClient
from .rpc import allow_rpc, serve_socket_rpc
from .scheduler import Scheduler
from .sqlite3 import DBSession
from .startup import startup_from_db
from .stepinfo import StepInfo
from .usage import CgroupMemorySampler, format_resource_usage
from .utils import positive_int
from .watcher import WATCHER_AVAILABLE, Watcher
from .workflow import Workflow

__all__ = ("DirectorHandler", "ServeConfig", "ServeResult", "interpret_jobs", "serve")


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


@attrs.define(frozen=True)
class ServeConfig:
    """The build policy of a single `serve()` call, as decided by the command line."""

    njob: int = attrs.field(default=1)
    """The maximum number of steps to run concurrently."""

    # Boolean flags below follow the `do_` prefix only where it disambiguates a flag
    # from a same-named noun elsewhere in the codebase (`do_clean`, `do_watch`);
    # otherwise a verb or adjective form is used.
    use_cgroup: bool = attrs.field(default=False)
    """If True, the director tracks peak memory usage through cgroups."""

    do_clean: bool = attrs.field(default=True)
    """If True, the director removes outdated output files."""

    use_duration: bool = attrs.field(default=True)
    """If True, the scheduler uses the duration of steps to optimize the execution order."""

    explain_rerun: bool = attrs.field(default=False)
    """Report detailed diagnostics explaining why a step is rerun rather than skipped."""

    keep_going: bool = attrs.field(default=False)
    """If True, keep dispatching new steps after another step has failed
    (like `make -k`). If False (default), the scheduler is put on hold after
    the first failure; steps already running are still allowed to finish."""

    fix_epoch: bool = attrs.field(default=True)
    """If True, set the `SOURCE_DATE_EPOCH` environment variable for step child
    processes (unless already set in the environment), for reproducible builds."""

    write_joblog: bool = attrs.field(default=False)
    """If True, record job-execution events (created, started, ended, completed) to
    `JOBLOG_CSV`, for diagnosing scheduler/executor dispatch overhead."""

    live_progress: bool = attrs.field(default=False)
    """Whether the reporter is an interactive terminal that wants live step-count updates."""

    do_watch: bool = attrs.field(default=False)
    """If True, the director alternates between build and watch phases until
    it receives an RPC to shutdown.
    If False, the director exits after a single build phase."""

    watch_first: bool = attrs.field(default=False)
    """If True, the builder restarts after the watcher sees the first file change."""

    available_resources: str | None = attrs.field(default=None)
    """Named resources and their available quantities, e.g. `"cpu:4,gpu:1"`, or `None`
    to declare no resources at all (any step that requests a named resource then
    never becomes runnable)."""

    defer_cap: int = attrs.field(default=100)
    """Maximum number of consecutive defers (since a step last succeeded) before
    it is failed instead of parked pending again. A livelock guard."""

    targets: list[Path] = attrs.field(factory=list)
    """Restrict the build to steps needed to produce these output files.
    An empty list builds the full default workflow."""

    target_dirs: list[Path] = attrs.field(factory=list)
    """Restrict the build to declared-DEFAULT steps whose output falls under one of
    these directories."""

    def __attrs_post_init__(self) -> None:
        if self.njob < 1:
            raise ValueError(f"Number of parallel tasks must be strictly positive, got {self.njob}")
        if self.watch_first and not self.do_watch:
            raise ValueError("watch_first cannot be set without do_watch.")

    @classmethod
    def from_args(cls, args: argparse.Namespace, njob: int) -> "ServeConfig":
        """Build the configuration from parsed command-line arguments."""
        return cls(
            njob=njob,
            use_cgroup=args.cgroup,
            do_clean=args.clean,
            use_duration=args.duration,
            explain_rerun=args.explain_rerun,
            keep_going=args.keep_going,
            fix_epoch=args.fix_epoch,
            write_joblog=args.joblog,
            live_progress=args.live_progress,
            do_watch=args.watch,
            watch_first=args.watch_first,
            available_resources=args.resources,
            defer_cap=args.defer_cap,
            targets=args.targets,
            target_dirs=args.target_dirs,
        )


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
        returncode = asyncio.run(async_main(args, db, mp_ctx))
    sys.exit(returncode)


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
        "--defer-cap",
        type=positive_int,
        default=100,
        help="Maximum number of consecutive defers (since the last success) before "
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


async def async_main(
    args: argparse.Namespace,
    db: DBSession,
    mp_ctx: multiprocessing.context.BaseContext | None = None,
) -> int:
    """Set up logging and the reporter, then run `serve()` and report its outcome.

    Returns
    -------
    returncode
        The exit code of the director process.
    """
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
                ServeConfig.from_args(args, njob),
                director_socket_path=args.director_socket,
                reporter=reporter,
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
    return serve_result.returncode.value


# Environment variables through which batch schedulers advertise the number of cores
# allocated to the current job, in order of priority.
SCHEDULER_CPU_ENV_VARS = ("SLURM_CPUS_PER_TASK", "PBS_NUM_PPN", "NCPUS")


def _get_ncore() -> int:
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
    return os.cpu_count() or 1


def interpret_jobs(jobs: Decimal) -> int:
    """Convert the command-line argument jobs into an integer."""
    ncore = _get_ncore() if jobs.as_tuple().exponent < 0 else 1
    return int(ncore * jobs)


async def serve(
    config: ServeConfig,
    *,
    director_socket_path: Path,
    reporter: ReporterClient,
    db: DBSession,
    mp_ctx: multiprocessing.context.BaseContext | None = None,
    handle_signals: bool = True,
) -> ServeResult:
    """Run the director: wire up the workflow components and drive the build to completion.

    Parameters
    ----------
    config
        The build policy for this run.
    director_socket_path
        The socket to listen to for remote calls.
    reporter
        The reporter client for sending information back to
        the terminal user interface.
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
    wtime_start = time.perf_counter()
    _check_plan()

    # Environment variables exported to step child processes (and forkserver children).
    # These are passed explicitly to the executor rather than set in `os.environ`,
    # so that running the director in-process (e.g. in the test suite) does not
    # pollute the calling process's environment.
    infra_env = {
        "STEPUP_DIRECTOR_SOCKET": str(director_socket_path),
        "STEPUP_ROOT": str(Path.cwd()),
        "STEPUP_LOG_LEVEL": logging.getLevelName(logging.root.level),
    }
    if config.fix_epoch and "SOURCE_DATE_EPOCH" not in os.environ:
        infra_env["SOURCE_DATE_EPOCH"] = "315532800"

    components = await _create_components(
        db=db,
        reporter=reporter,
        config=config,
        infra_env=infra_env,
        mp_ctx=mp_ctx,
    )
    # The RPC facade over the components, built on top of them instead of being one of them:
    # it serves remote calls and terminal signals, and nothing in the graph depends on it.
    handler = DirectorHandler(
        scheduler=components.scheduler,
        workflow=components.workflow,
        db=db,
        reporter=reporter,
        executor=components.executor,
        builder=components.builder,
        watcher=components.watcher,
        stop_event=components.stop_event,
    )

    # Define the initial plan.py as static file and create a step for it.
    async with db:
        initialized = components.workflow.initialize_boot()
    if initialized:
        await reporter("STARTUP", "(Re)initialized boot script")
        components.builder.resume.set()
    else:
        await startup_from_db(components.workflow, db, reporter, components.builder)

    # Validate targets against the (re)loaded graph and flag affected steps for
    # recompute. Must run after the boot/resume block above: on a resumed database,
    # startup_from_db's file scan is what marks a changed plan.py's step PENDING, which
    # Workflow.reconcile_targets()'s creator-chain guard consults. Must run before the
    # task gather below, since the builder loop (and thus the first dispatch) only
    # starts there.
    async with db:
        try:
            components.workflow.reconcile_targets()
        except GraphError as exc:
            await reporter("ERROR", f"Invalid build target: {exc}")
            await reporter.check_logs()
            return ServeResult(returncode=ReturnCode.FAILED, usage_report="", usage_summary="")

    await _run_tasks(
        components,
        handler,
        db=db,
        director_socket_path=director_socket_path,
        watch_first=config.watch_first,
        handle_signals=handle_signals,
    )

    usage_report, usage_summary = format_resource_usage(
        wtime_start,
        components.executor.step_accumulator,
        components.memory_sampler,
    )

    return ServeResult(
        returncode=components.builder.returncode,
        usage_report=usage_report,
        usage_summary=usage_summary,
    )


@attrs.define(frozen=True)
class _Components:
    """The long-lived objects that make up a running director.

    The `DirectorHandler` is not one of them: it is the RPC facade over these objects,
    constructed in `serve` once they exist.
    """

    workflow: Workflow = attrs.field()
    scheduler: Scheduler = attrs.field()
    executor: Executor = attrs.field()
    builder: Builder = attrs.field()
    watcher: Watcher | None = attrs.field()
    memory_sampler: CgroupMemorySampler | None = attrs.field()
    stop_event: asyncio.Event = attrs.field()


async def _create_components(
    *,
    db: DBSession,
    reporter: ReporterClient,
    config: ServeConfig,
    infra_env: dict[str, str],
    mp_ctx: multiprocessing.context.BaseContext | None,
) -> _Components:
    """Construct and wire the long-lived objects that make up a running director."""
    dir_queue = asyncio.Queue() if config.do_watch else None
    workflow = Workflow(
        db,
        dir_queue=dir_queue,
        defer_cap=config.defer_cap,
        targets=config.targets,
        target_dirs=config.target_dirs,
    )
    await workflow.initialize()
    scheduler = Scheduler(
        workflow, db=db, use_duration=config.use_duration, write_joblog=config.write_joblog
    )
    if config.available_resources is not None:
        await reporter("DIRECTOR", f"Setting available resources: {config.available_resources}")
    await scheduler.initialize(config.available_resources)
    executor = Executor(
        scheduler=scheduler,
        workflow=workflow,
        db=db,
        reporter=reporter,
        mp_ctx=mp_ctx,
        explain_rerun=config.explain_rerun,
        keep_going=config.keep_going,
        live_progress=config.live_progress,
        write_joblog=config.write_joblog,
        infra_env=infra_env,
    )
    # Builder is agnostic of watch mode; it is built first because the watcher needs
    # builder.hash_queue, sharing the wake event that a hash-job submission uses to
    # nudge a parked job_loop.
    builder = Builder(
        njob=config.njob,
        scheduler=scheduler,
        workflow=workflow,
        db=db,
        reporter=reporter,
        live_progress=config.live_progress,
        do_remove_outdated=config.do_clean,
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
            njob=config.njob,
        )
        if config.do_watch
        else None
    )
    memory_sampler = CgroupMemorySampler() if config.use_cgroup else None
    return _Components(
        workflow=workflow,
        scheduler=scheduler,
        executor=executor,
        builder=builder,
        watcher=watcher,
        memory_sampler=memory_sampler,
        stop_event=asyncio.Event(),
    )


async def _run_tasks(
    components: _Components,
    handler: "DirectorHandler",
    *,
    db: DBSession,
    director_socket_path: Path,
    watch_first: bool,
    handle_signals: bool,
) -> None:
    """Run the director's async tasks until shutdown, then tear them down in order."""
    exit_event = asyncio.Event()
    rpc_server = asyncio.create_task(serve_socket_rpc(handler, director_socket_path, exit_event))
    coroutines = [
        build_loop(components.builder, components.watcher, components.stop_event),
        db.database_maintenance_loop(components.stop_event),
    ]
    if components.memory_sampler is not None:
        coroutines.append(components.memory_sampler.loop(components.stop_event))
    if components.watcher is not None:
        coroutines.append(components.watcher.loop(components.stop_event))
        if watch_first:
            coroutines.append(watch_first_loop(components.watcher, handler, components.stop_event))
    # Abort the build on a terminal signal, instead of dying with a KeyboardInterrupt
    # traceback (SIGINT) or instantly and mid-transaction (SIGTERM).
    loop = asyncio.get_running_loop()
    if handle_signals:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, handler.interrupt, sig)
        # Stop the running steps along with this process, which the terminal cannot do itself.
        loop.add_signal_handler(signal.SIGTSTP, handler.suspend)
    try:
        await asyncio.gather(*coroutines)
    finally:
        # In case of an exception, set the stop event, so other parts know they can stop waiting.
        components.stop_event.set()
        # Regular shutdown. The signal handlers stay installed for its duration:
        # a step ignoring the first interrupt is killed by a second one during `builder.stop()`.
        await components.builder.stop()
        exit_event.set()
        await rpc_server
        director_socket_path.remove_p()
        await handler.cancel_interrupt()
        if handle_signals:
            for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGTSTP):
                loop.remove_signal_handler(sig)


def _check_plan():
    """Check that the boot script exists, is executable and has the expected shebang."""
    if not PLAN_PY.is_file():
        raise ValueError(f"Is not a file: {PLAN_PY}")
    if not os.access(PLAN_PY, os.X_OK):
        raise ValueError(f"File is not executable: {PLAN_PY}")
    with open(PLAN_PY) as fh:
        shebang = "#!/usr/bin/env python3"
        if not fh.readline().rstrip() == shebang:
            raise ValueError(f"First line of plan differs from '{shebang}': {PLAN_PY}")


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


async def watch_first_loop(watcher: Watcher, handler: "DirectorHandler", stop_event: asyncio.Event):
    """When a file of the watcher has changed, call the builder after 0.5 seconds delay."""
    changed_event = asyncio.Event()
    watcher.files_changed_events.add(changed_event)
    while True:
        await watcher.active.wait()
        await wait_for_events(changed_event, stop_event, return_when=asyncio.FIRST_COMPLETED)
        if stop_event.is_set():
            break
        await asyncio.sleep(0.5)
        await handler.run()


@attrs.define
class DirectorHandler:
    scheduler: Scheduler = attrs.field(kw_only=True)
    workflow: Workflow = attrs.field(kw_only=True)
    db: DBSession = attrs.field(kw_only=True)
    reporter: ReporterClient = attrs.field(kw_only=True)
    executor: Executor = attrs.field(kw_only=True)
    builder: Builder = attrs.field(kw_only=True)
    watcher: Watcher | None = attrs.field(kw_only=True)
    stop_event: asyncio.Event = attrs.field(kw_only=True)
    _next_step_signal: signal.Signals = attrs.field(init=False, default=signal.SIGINT)
    """The signal the next escalating `shutdown` sends to running steps.

    Starts at `SIGINT` and moves to `SIGKILL` once a `SIGINT` has been delivered to the
    steps, by either `shutdown` or `_interrupt`.
    Only `shutdown` reads it: `_interrupt` always opens with `SIGINT` and escalates on a
    timer instead, see its docstring.
    """

    _interrupt_count: int = attrs.field(init=False, default=0)
    """The number of terminal signals received so far, see `interrupt`."""

    _interrupt_task: asyncio.Task | None = attrs.field(init=False, default=None)
    """The in-flight `_interrupt` task, kept alive here so it cannot be garbage-collected."""

    _interrupt_escalate: asyncio.Event = attrs.field(init=False, factory=asyncio.Event)
    """Set by a second terminal signal, to cut the grace period of the first one short."""

    _resume_tasks: set[asyncio.Task] = attrs.field(init=False, factory=set)
    """In-flight resume reports of `suspend`, kept alive here so they cannot be
    garbage-collected mid-send (same rationale as `Executor._counts_flush_tasks`)."""

    #
    # Building the workflow
    #

    def _submit_to_check(self, to_check: Mapping[str, FileHash]) -> None:
        """Submit a hash job for each `path: old_hash` entry, applied with `cause=CONFIRMED`.

        Fire-and-forget: the caller does not wait for these jobs. Each job's own
        completion flips the file from `UNCONFIRMED` to `STATIC` or `MISSING` and wakes
        `job_loop`, which is what lets a step consuming the file become runnable.
        """
        for path, old_hash in to_check.items():
            self.builder.hash_queue.submit(path, old_hash, HashUpdateCause.CONFIRMED)

    @allow_rpc
    async def static(
        self,
        job_i: int,
        tree_paths: list[str],
        file_paths: list[str],
        patterns: list[tuple[str, list[str]]],
    ) -> None:
        """Register the static trees, static files and glob patterns of one `static()` call.

        Parameters
        ----------
        job_i
            The job index of the calling step.
        tree_paths
            Directories, given literally or matched by a glob pattern: each becomes a
            static tree. Sorted, so a parent is always registered before a child it
            contains, which turns an overlap within one call into a no-op instead of a
            "parent directory of an existing static tree" error.
        file_paths
            Files to declare `UNCONFIRMED`, to be confirmed as `STATIC` or `MISSING` by a
            hash job submitted in the background. The `UNCONFIRMED` intermediate state
            avoids unnecessary file hash calculations: when size, inode and mtime are
            unchanged, the old hash is reused.
        patterns
            `(pattern, matches)` pairs, one per glob pattern given to `static()`.
            The matches are recorded with the pattern, so a later run can tell
            whether the match set changed. A pattern never carries a `subs` dict,
            because `static()` takes no keyword arguments, so `NamedGlob(pattern)`
            rebuilds exactly what the client globbed with.

        Notes
        -----
        Everything happens in a single transaction, so the tree-before-file ordering
        is a guarantee of this method rather than a convention spread over two round
        trips from the client.
        """
        to_check = {}
        async with self.db:
            creator = self.scheduler.get_step(job_i)
            for path in tree_paths:
                to_check.update(self.workflow.register_static_tree(creator, path))
            to_check.update(self.workflow.declare_static_files(creator, file_paths))
            for pattern, matches in patterns:
                ng = NamedGlob(pattern)
                ng.extend(matches)
                self.workflow.register_nglob(creator, ng)
        self._submit_to_check(to_check)

    @allow_rpc
    async def glob(self, job_i: int, pattern: str, subs: dict[str, str], paths: list[str]) -> None:
        """Register a glob pattern with the calling step and validate its matches.

        A glob pattern is a pure query: it declares nothing and owns nothing.
        The pattern is recorded so the calling step becomes pending when the match set
        changes, and its matches are validated against what the graph already knows.

        Parameters
        ----------
        job_i
            The job index of the calling step.
        pattern
            The glob pattern, relative to `${STEPUP_ROOT}`, with its trailing separator
            preserved when it is a directory pattern.
        subs
            The sub-pattern of each named wildcard, as given to `glob()`.
        paths
            Every match, sorted, directories with a trailing separator.
        """
        ng = NamedGlob(pattern, subs)
        ng.extend(paths)
        async with self.db:
            creator = self.scheduler.get_step(job_i)
            self.workflow.register_nglob(creator, ng)

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
        shell: bool = False,
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
                shell=shell,
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

        When some dynamic inputs are still `UNCONFIRMED` (matches of a static tree not yet hashed),
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
            self.executor.defer(job_i, unavailable=unavailable, unfresh=unfresh)
        if is_detached:
            carry_on = False
        return carry_on

    @allow_rpc
    async def defer_step(self, job_i: int, missing: list[str]) -> None:
        """Defer a step due to unavailable dependencies."""
        self.executor.defer(job_i, unavailable=missing)

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
        This is an RPC wrapper for `Step.add_subprocess`.
        The recorded metadata is informative for archival and debugging, not authoritative.
        """
        async with self.db:
            step = self.scheduler.get_step(job_i)
            step.add_subprocess(
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
    async def get_info(self, job_i: int) -> StepInfo:
        """Return step information, consistent with return values of functions in stepup.core.api.

        For the sake of consistency, dynamic dependencies are not included.
        """
        async with self.db:
            step = self.scheduler.get_step(job_i)
            return step.get_info()

    #
    # Termination and lifecycle
    #

    def _stop_scheduling(self) -> None:
        """Stop dispatching new work and end the watch phase, without touching running steps.

        Shared by both termination routes: `shutdown` (the `q` key) and
        `interrupt` (a terminal signal).
        Whether and how running steps are then signalled is the caller's policy.
        """
        self.scheduler.on_hold = True
        self.stop_event.set()
        if self.watcher is not None:
            self.watcher.interrupt.set()

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
        # Stop dispatching before anything else: the reporter calls below are real awaits,
        # during which the builder's job loop would otherwise still start new steps.
        self.scheduler.on_hold = True
        if self.stop_event.is_set():
            sig = self._next_step_signal
            await self.reporter("DIRECTOR", f"Interrupting running steps ({sig.name}).")
            self.executor.interrupt(sig)
            self._next_step_signal = signal.SIGKILL
            if self.watcher is not None:
                self.watcher.interrupt.set()
        else:
            if len(self.builder.running_tasks) > 0:
                await self.reporter("DIRECTOR", "Waiting for steps to complete before shutdown.")
            self._stop_scheduling()

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
        self._stop_scheduling()
        # Steps run in a session of their own, so the terminal does not signal them:
        # this is the only thing that stops them, on every route (Ctrl-C, SIGTERM, or a
        # SIGINT sent to the terminal user interface alone).
        self.executor.interrupt(signal.SIGINT)
        # A `q` press after this must escalate to SIGKILL, not re-send SIGINT.
        self._next_step_signal = signal.SIGKILL
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

    def suspend(self) -> None:
        """Suspend the whole build after a `SIGTSTP`, and resume it when continued.

        Steps run in a session of their own, so the terminal never stops them:
        just like `interrupt`, this is the only thing that reaches them.
        Stopping this process is done by re-raising `SIGTSTP` with the default disposition,
        the canonical way for a program to honour a suspension, rather than by `SIGSTOP`:
        the shell tracks the terminal user interface as a job, and `fg` continues the whole
        process group, this process included.

        The steps are resumed on the line after the self-stop instead of from a `SIGCONT`
        handler, which keeps both halves in one readable block. It also degrades gracefully
        when this process group is orphaned (`setsid stepup build`): the kernel then
        discards the re-raised `SIGTSTP`, `os.kill` returns straight away and the steps are
        resumed immediately, matching the terminal user interface, which is not stopped
        in that situation either.

        Nothing is reported here: the reporter socket is served by the terminal user
        interface, which is stopping at this very moment, so awaiting it would block this
        process on its way down. The user-visible message is sent on resume instead.

        This is a plain callback (not a coroutine), as required by `add_signal_handler`.
        Blocking the event loop is the point: the process must not do anything else while
        it is suspended.
        """
        loop = asyncio.get_running_loop()
        self.executor.suspend()
        logger.info("Suspending the build (SIGTSTP).")
        loop.remove_signal_handler(signal.SIGTSTP)
        try:
            os.kill(os.getpid(), signal.SIGTSTP)
        finally:
            loop.add_signal_handler(signal.SIGTSTP, self.suspend)
            nrun, seconds = self.executor.resume()
            logger.info("Resumed %d job(s) after %.1f s.", nrun, seconds)
            if nrun > 0 and not self.stop_event.is_set():
                task = asyncio.create_task(
                    self.reporter("DIRECTOR", f"Resumed {nrun} step(s) after {seconds:.1f} s."),
                    name="resume-report",
                )
                self._resume_tasks.add(task)
                task.add_done_callback(self._resume_tasks.discard)

    async def cancel_interrupt(self) -> None:
        """Cancel the pending grace period of `interrupt`, e.g. when the build ended in time."""
        if self._interrupt_task is not None and not self._interrupt_task.done():
            self._interrupt_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._interrupt_task
        self._interrupt_task = None

    #
    # Interactive use
    #

    async def _wait_watch_active(self) -> None:
        """Block until the watch phase starts or the director stops.

        Returns immediately when the director runs without a watcher.
        """
        if self.watcher is None:
            return
        await wait_for_events(
            self.watcher.active, self.stop_event, return_when=asyncio.FIRST_COMPLETED
        )

    async def _watch_change(self, path: str, observed: set[Path]) -> None:
        """Block until `path` shows up in `observed`, a live set owned by the watcher."""
        path = Path(path).normpath()
        await self._wait_watch_active()
        event = asyncio.Event()
        self.watcher.files_changed_events.add(event)
        try:
            while True:
                if path in observed:
                    return
                await event.wait()
                event.clear()
        finally:
            self.watcher.files_changed_events.discard(event)

    @allow_rpc
    async def drain(self) -> None:
        """Do not start new steps and switch to the watch phase after the build phase completes.

        Notes
        -----
        This RPC blocks until all running steps have completed.
        """
        self.scheduler.on_hold = True
        await self._wait_watch_active()

    @allow_rpc
    async def join(self) -> None:
        """Block until the builder completed all (runnable) steps and shut down."""
        if self.watcher is not None:
            await self._wait_watch_active()
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
        await self.reporter(
            "DIRECTOR",
            f"Wrote graph to {prefix}.txt, {prefix}_provenance.dot and {prefix}_dependency.dot",
        )

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
        if self.watcher is not None:
            await self._watch_change(path, self.watcher.updated)

    @allow_rpc
    async def watch_delete(self, path: str) -> None:
        """Block until the watcher observed the deletion of the file."""
        if self.watcher is not None:
            await self._watch_change(path, self.watcher.deleted)

    @allow_rpc
    async def wait(self) -> None:
        """Block until the builder completed all (runnable) steps."""
        await self._wait_watch_active()


if __name__ == "__main__":
    main()
