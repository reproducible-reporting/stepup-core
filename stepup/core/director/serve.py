# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Wire up the components of the director and drive the build to completion."""

import argparse
import asyncio
import functools
import logging
import multiprocessing
import os
import signal
import time
from decimal import Decimal

import attrs

from ..constants import PLAN_PY
from ..enums import ReturnCode
from ..exceptions import CgroupError, GraphError
from ..graph.sqlite3 import DBSession
from ..graph.workflow import Workflow
from ..path import Path
from ..platform.asyncio import signal_handlers, wait_for_any_event
from ..platform.cgroups import get_ncore_from_cgroup
from ..platform.oscompat import abort_signals, suspend_available
from ..rpc.server import SocketRPCServer
from .builder import Builder
from .executor import Executor
from .handler import DirectorHandler
from .reporter import ReporterClient
from .scheduler import Scheduler
from .startup import resume_from_db, update_startup_i
from .usage import CgroupMemorySampler, finalize_resource_usage
from .watcher import Watcher

__all__ = (
    "ServeConfig",
    "ServeResult",
    "interpret_jobs",
    "serve",
)

logger = logging.getLogger(__name__)


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
    """Whether to track peak memory usage through cgroups."""

    do_clean: bool = attrs.field(default=True)
    """Whether to remove outdated output files."""

    use_duration: bool = attrs.field(default=True)
    """Whether to use the duration of steps to optimize the execution order."""

    explain_rerun: bool = attrs.field(default=False)
    """Report detailed diagnostics explaining why a step is rerun rather than skipped."""

    keep_going: bool = attrs.field(default=False)
    """Whether to keep dispatching new steps after another step has failed (like `make -k`).
    If False (default), the scheduler starts draining after the first failure;
    steps already running are still allowed to finish."""

    fix_epoch: bool = attrs.field(default=True)
    """Whether to set the `SOURCE_DATE_EPOCH` environment variable for step child processes
    (unless already set in the environment), for reproducible builds."""

    offline: bool = attrs.field(default=False)
    """Whether external data cannot be reached.
    If True, `STEPUP_STARTUP_I` keeps its value, at startup and at every recheck,
    and the steps find `STEPUP_BUILD_OFFLINE=1` in their environment."""

    write_joblog: bool = attrs.field(default=False)
    """Whether to record job-execution events (init, created, started, ended, completed)
    to `JOBLOG_CSV`, for diagnosing scheduler/executor dispatch overhead."""

    live_progress: bool = attrs.field(default=False)
    """Whether the reporter's console is an interactive terminal
    that wants live step-count updates."""

    do_watch: bool = attrs.field(default=False)
    """Whether to alternate between build and watch phases
    until an RPC to shut down is received.
    If False, the director exits after a single build phase."""

    watch_first: bool = attrs.field(default=False)
    """Whether to restart the builder after the watcher sees the first file change."""

    available_resources: str | None = attrs.field(default=None)
    """Named resources and their available quantities, e.g. `"cpu:4,gpu:1"`,
    or `None` to declare no resources at all
    (any step that requests a named resource then never becomes runnable)."""

    defer_cap: int = attrs.field(default=100)
    """Maximum number of consecutive defers (since a step last succeeded)
    before it is failed instead of parked pending again.
    A livelock guard."""

    targets: list[Path] = attrs.field(factory=list)
    """Restrict the build to steps needed to produce these output files.
    An empty list builds the full default workflow."""

    target_dirs: list[Path] = attrs.field(factory=list)
    """Restrict the build to declared-DEFAULT steps
    whose output falls under one of these directories."""

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
            offline=args.offline,
            write_joblog=args.joblog,
            live_progress=args.live_progress,
            do_watch=args.watch,
            watch_first=args.watch_first,
            available_resources=args.resources,
            defer_cap=args.defer_cap,
            targets=args.targets,
            target_dirs=args.target_dirs,
        )


# Environment variables through which batch schedulers advertise the number of cores
# allocated to the current job, in order of priority.
SCHEDULER_CPU_ENV_VARS = ("SLURM_CPUS_PER_TASK", "PBS_NUM_PPN", "NCPUS")


def _get_ncore() -> int:
    """Determine the number of CPU cores available to this process.

    Cgroup v2 accounting (`cpuset.cpus.effective` / `cpu.max`) is tried first,
    since that reflects the containment actually applied to this process, e.g. by Slurm or PBS.
    Scheduler-provided environment variables come next
    (they are only advisory and can disagree with the actual cgroup containment),
    and the OS-reported core count comes last,
    because a batch scheduler may allocate fewer cores than are physically present on the node.
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
    # `os.process_cpu_count` is new in Python 3.13 and respects the CPU affinity on Windows.
    process_cpu_count = getattr(os, "process_cpu_count", os.cpu_count)
    return process_cpu_count() or 1


def interpret_jobs(jobs: Decimal) -> int:
    """Convert the `jobs` command-line argument into an integer."""
    ncore = _get_ncore() if jobs.as_tuple().exponent < 0 else 1
    return int(ncore * jobs)


async def serve(
    config: ServeConfig,
    *,
    director_socket_path: Path,
    rpc_transport: str,
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
        The socket path at which to listen for remote calls.
    rpc_transport
        The transport to listen on, see `stepup.core.rpc.transport.select_rpc_transport`.
    reporter
        The reporter client for sending information back to the terminal user interface.
    db
        The database session backing the workflow graph.
    mp_ctx
        A `multiprocessing` forkserver context for Python step execution,
        or `None` to use plain subprocesses.
    handle_signals
        Whether to install handlers for the signals that abort the build
        (see `stepup.core.platform.oscompat.abort_signals` and `DirectorHandler.interrupt`)
        and, where a process can be suspended, for `SIGTSTP` (see `DirectorHandler.suspend`).
        Set to False when running the director inside another process (e.g. the test suite),
        where hijacking the process-wide signal handlers is not wanted.

    Returns
    -------
    result
        The exit code of the director process,
        together with a resource-usage summary collected over the lifetime of this call:

        - wall time
        - CPU time (user/system)
        - peak memory for the director (and optionally its step child processes).
    """
    wtime_start = time.perf_counter()
    _check_plan()

    # Environment variables exported to step child processes (and forkserver children).
    # They form an overlay of the workflow rather than being set in `os.environ`,
    # so that running the director in-process (e.g. in the test suite)
    # does not pollute the calling process's environment.
    # `Workflow.assign_startup_i()` adds `STEPUP_STARTUP_I` once the database is open.
    infra_env = {
        "STEPUP_DIRECTOR_SOCKET": str(director_socket_path),
        "STEPUP_ROOT": str(Path.cwd()),
        "STEPUP_BUILD_LOG_LEVEL": logging.getLevelName(logging.root.level),
        "STEPUP_BUILD_OFFLINE": "1" if config.offline else "0",
    }
    if config.fix_epoch and "SOURCE_DATE_EPOCH" not in os.environ:
        infra_env["SOURCE_DATE_EPOCH"] = "315532800"

    memory_sampler = CgroupMemorySampler() if config.use_cgroup else None
    handler = await _wire_director(
        db=db,
        reporter=reporter,
        config=config,
        infra_env=infra_env,
        mp_ctx=mp_ctx,
    )

    # The scan of an existing workflow runs at every start, before `initialize_boot()`,
    # so that a `plan.py` that the previous director saw deleted, and that was restored since,
    # is confirmed again instead of making `initialize_boot()` start over.
    # On a new database, the scan finds nothing and reports nothing.
    await resume_from_db(handler.workflow, reporter, handler.builder)
    # Define the initial plan.py as a static file and create a step for it.
    async with db:
        initialized = handler.workflow.initialize_boot()
    if initialized:
        await reporter("STARTUP", "(Re)initialized boot script")
    # Every step that must run again is pending now, so the builder can start.
    handler.builder.resume.set()

    # Targets must be reconciled after the boot script is defined.
    # See `Workflow.reconcile_targets()` for details.
    async with db:
        try:
            handler.workflow.reconcile_targets()
        except GraphError as exc:
            await reporter("ERROR", f"Invalid build target: {exc}")
            await reporter.warn_about_logs()
            return ServeResult(returncode=ReturnCode.FAILED, usage_report="", usage_summary="")

    await _run_tasks(
        handler,
        memory_sampler,
        director_socket_path=director_socket_path,
        rpc_transport=rpc_transport,
        watch_first=config.watch_first,
        handle_signals=handle_signals,
    )

    usage_report, usage_summary = finalize_resource_usage(
        wtime_start,
        handler.executor.step_usage,
        memory_sampler,
    )

    return ServeResult(
        returncode=handler.builder.returncode,
        usage_report=usage_report,
        usage_summary=usage_summary,
    )


async def _wire_director(
    *,
    db: DBSession,
    reporter: ReporterClient,
    config: ServeConfig,
    infra_env: dict[str, str],
    mp_ctx: multiprocessing.context.BaseContext | None,
) -> "DirectorHandler":
    """Construct and wire the long-lived objects that make up a running director.

    Returns
    -------
    handler
        The RPC handler, through which every wired component is reachable.
    """
    dir_queue = asyncio.Queue() if config.do_watch else None
    workflow = Workflow(
        db,
        dir_queue=dir_queue,
        defer_cap=config.defer_cap,
        targets=config.targets,
        target_dirs=config.target_dirs,
        infra_env=infra_env,
    )
    await workflow.initialize()
    # The number is assigned as soon as its table exists, long before the first step runs.
    await update_startup_i(workflow, reporter, offline=config.offline)
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
    )
    # Builder is agnostic of watch mode;
    # it is built first because the watcher needs builder.hash_queue,
    # which shares the wake event that a hash-job submission uses to nudge a parked job_loop.
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
            workflow=workflow,
            db=db,
            reporter=reporter,
            dir_queue=dir_queue,
            executor=executor,
            hash_queue=builder.hash_queue,
            njob=config.njob,
        )
        if config.do_watch
        else None
    )
    return DirectorHandler(
        scheduler=scheduler,
        workflow=workflow,
        db=db,
        reporter=reporter,
        executor=executor,
        builder=builder,
        watcher=watcher,
        stop_event=asyncio.Event(),
        offline=config.offline,
    )


async def _run_tasks(
    handler: "DirectorHandler",
    memory_sampler: CgroupMemorySampler | None,
    *,
    director_socket_path: Path,
    rpc_transport: str,
    watch_first: bool,
    handle_signals: bool,
) -> None:
    """Run the director's async tasks until shutdown, then tear them down in order."""
    exit_event = asyncio.Event()
    server = SocketRPCServer(handler, director_socket_path, rpc_transport)
    # The steps started by `build_loop` connect to the director as soon as they run.
    await server.listen()
    rpc_server = asyncio.create_task(server.serve(exit_event))
    coroutines = [
        build_loop(handler.builder, handler.watcher, handler.stop_event),
        handler.db.reclaim_loop(handler.stop_event),
    ]
    if memory_sampler is not None:
        coroutines.append(memory_sampler.loop(handler.stop_event))
    if handler.watcher is not None:
        coroutines.append(handler.watcher.loop(handler.stop_event))
        if watch_first:
            coroutines.append(watch_first_loop(handler.watcher, handler, handler.stop_event))
    callbacks = {}
    if handle_signals:
        # Abort the build on a terminal signal,
        # instead of dying with a KeyboardInterrupt traceback (SIGINT)
        # or instantly and mid-transaction (SIGTERM).
        callbacks.update(
            (sig, functools.partial(handler.interrupt, sig)) for sig in abort_signals()
        )
        if suspend_available():
            # Stop the running steps along with this process,
            # which the terminal cannot do itself.
            callbacks[signal.SIGTSTP] = handler.suspend
    with signal_handlers(callbacks):
        try:
            await asyncio.gather(*coroutines)
        finally:
            # In case of an exception, set the stop event,
            # so other parts know they can stop waiting.
            handler.stop_event.set()
            # Regular shutdown.
            # The signal handlers stay installed for its duration: a step ignoring
            # the first interrupt is killed by a second one during `builder.stop()`.
            await handler.builder.stop()
            exit_event.set()
            await rpc_server
            director_socket_path.remove_p()
            await handler.cancel_interrupt()


def _check_plan():
    """Check that the boot script exists, is executable and has the expected shebang."""
    if not PLAN_PY.is_file():
        raise ValueError(f"Is not a file: {PLAN_PY}")
    if not os.access(PLAN_PY, os.X_OK):
        raise ValueError(f"File is not executable: {PLAN_PY}")
    with open(PLAN_PY, encoding="utf-8") as fh:
        shebang = "#!/usr/bin/env python3"
        if not fh.readline().rstrip() == shebang:
            raise ValueError(f"First line of plan differs from '{shebang}': {PLAN_PY}")


async def build_loop(builder: Builder, watcher: Watcher | None, stop_event: asyncio.Event):
    """Repeatedly run build phases until `stop_event` fires.

    `Builder` itself has no notion of watch mode, so this is where that policy lives:
    after each phase, hand off to `watcher` to resume file-system monitoring, or,
    without a watcher, stop after a single phase.
    """
    while await builder.run_once(stop_event):
        if watcher is None:
            stop_event.set()
        else:
            watcher.start_watching.set()


async def watch_first_loop(watcher: Watcher, handler: "DirectorHandler", stop_event: asyncio.Event):
    """Run pending steps 0.5 seconds after the watcher observes a file change."""
    with watcher.subscribe_changes() as changed_event:
        while True:
            # Every wait below must also watch `stop_event`,
            # because the director shuts down only after this loop has ended.
            # Once the shutdown has begun, the watch phase never starts again,
            # so waiting for it alone would block the shutdown forever.
            await wait_for_any_event(watcher.busy_watching, stop_event)
            if stop_event.is_set():
                break
            await wait_for_any_event(changed_event, stop_event)
            if stop_event.is_set():
                break
            await asyncio.sleep(0.5)
            await handler.start_build_phase()
