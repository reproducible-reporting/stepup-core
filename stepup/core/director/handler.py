# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""The RPC interface of the director."""

import asyncio
import contextlib
import logging
import os
import signal
from collections.abc import Mapping
from itertools import chain

import attrs

from ..enums import FileState, Need, StepState, StopLevel
from ..graph.file import File
from ..graph.sqlite3 import DBSession
from ..graph.workflow import Workflow
from ..hash import FileHash
from ..launch.worker import describe_stop_level
from ..nglob import NamedGlob
from ..path import Path
from ..platform.asyncio import wait_for_any_event
from ..rpc.server import allow_rpc
from ..stepinfo import StepInfo
from .builder import Builder
from .executor import Executor
from .reporter import ReporterClient
from .scheduler import Scheduler
from .startup import rescan_env_vars, update_startup_i
from .watcher import Watcher

__all__ = ("DirectorHandler",)

logger = logging.getLogger(__name__)


INTERRUPT_GRACE = 5.0
"""Seconds a step gets to wind down after a terminal signal, before it is killed.

See `DirectorHandler.interrupt`.
"""


@attrs.define
class DirectorHandler:
    """The director's RPC interface, and the handle on its long-lived components.

    The components are wired to each other, not to this class:
    it serves remote calls and terminal signals,
    and nothing in the graph depends on it.
    """

    scheduler: Scheduler = attrs.field(kw_only=True)
    workflow: Workflow = attrs.field(kw_only=True)
    db: DBSession = attrs.field(kw_only=True)
    reporter: ReporterClient = attrs.field(kw_only=True)
    executor: Executor = attrs.field(kw_only=True)
    builder: Builder = attrs.field(kw_only=True)
    watcher: Watcher | None = attrs.field(kw_only=True)
    stop_event: asyncio.Event = attrs.field(kw_only=True)
    offline: bool = attrs.field(kw_only=True, default=False)
    """Whether external data cannot be reached, so that a recheck keeps `STEPUP_STARTUP_I`.

    See `start_build_phase`.
    """

    _build_phase_lock: asyncio.Lock = attrs.field(init=False, factory=asyncio.Lock)
    """Held by `start_build_phase` from its check for the watch phase until the builder resumes.

    Two calls could otherwise both find the watch phase active,
    and one could then mark steps pending while the builder already runs.
    """

    _next_stop_level: StopLevel = attrs.field(init=False, default=StopLevel.INTERRUPT)
    """The stop level the next escalating `shutdown` delivers to running steps.

    Starts at `INTERRUPT` (`SIGINT` on POSIX) and moves to `KILL` (`SIGKILL`)
    once the steps have been interrupted, by either `shutdown` or `_interrupt`.
    Only `shutdown` reads it:
    `_interrupt` always opens with `INTERRUPT` and escalates on a timer instead.
    See its docstring.
    `stepup.core.launch.worker` documents what delivers each level on each platform.
    """

    _interrupt_count: int = attrs.field(init=False, default=0)
    """The number of terminal signals received so far.
    See `interrupt`."""

    _interrupt_task: asyncio.Task | None = attrs.field(init=False, default=None)
    """The in-flight `_interrupt` task, kept alive here so it cannot be garbage-collected."""

    _interrupt_escalate: asyncio.Event = attrs.field(init=False, factory=asyncio.Event)
    """Set by a second terminal signal, to cut the grace period of the first one short."""

    _resume_tasks: set[asyncio.Task] = attrs.field(init=False, factory=set)
    """In-flight resume reports of `suspend`,
    kept alive here so they cannot be garbage-collected mid-send
    (same rationale as `Executor._counts_flush_tasks`)."""

    #
    # RPC from steps: workflow construction
    #

    def _submit_to_check(self, to_check: Mapping[str, FileHash]) -> None:
        """Submit a hash job for each `path: old_hash` entry, to settle whether the file exists.

        Fire-and-forget: the caller does not wait for these jobs.
        `Builder.handle_done_tasks` applies their results, which flips each file from
        `UNCONFIRMED` to `CONFIRMED` or `MISSING`, and it does so before `job_loop` picks the
        next job, which is what lets a step consuming the file become runnable.
        """
        for path, old_hash in to_check.items():
            self.builder.hash_queue.submit(path, old_hash)

    @allow_rpc
    async def declare_static(
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
            Directories, given literally or matched by a glob pattern:
            each becomes a static tree.
            Sorted, so a parent is always registered before a child it contains,
            which turns an overlap within one call into a no-op
            instead of a "parent directory of an existing static tree" error.
        file_paths
            Files to declare `UNCONFIRMED`,
            to be resolved to `CONFIRMED` or `MISSING` by a hash job submitted in the background.
            The `UNCONFIRMED` intermediate state avoids unnecessary file hash calculations:
            when size, inode and mtime are unchanged, the old hash is reused.
        patterns
            `(pattern, matches)` pairs, one per glob pattern given to `static()`.
            The matches are recorded with the pattern,
            so a later run can tell whether the match set changed.
            A pattern never carries a `subs` dict, because `static()` takes no keyword arguments,
            so `NamedGlob(pattern)` rebuilds exactly what the client globbed with.
        """
        to_check = {}
        async with self.db:
            creator = self.scheduler.get_job_step(job_i)
            for path in tree_paths:
                to_check.update(self.workflow.register_static_tree(creator, path))
            to_check.update(self.workflow.declare_static_files(creator, file_paths))
            for pattern, matches in patterns:
                ng = NamedGlob(pattern)
                ng.extend(matches)
                self.workflow.register_nglob(creator, ng)
        self._submit_to_check(to_check)

    @allow_rpc
    async def register_glob(
        self, job_i: int, pattern: str, subs: dict[str, str], paths: list[str]
    ) -> None:
        """Register a glob pattern with the calling step and validate its matches.

        A glob pattern is a pure query: it declares nothing and owns nothing.
        The pattern is recorded so the calling step becomes pending when the match set changes,
        and its matches are validated against what the graph already knows.

        Parameters
        ----------
        job_i
            The job index of the calling step.
        pattern
            The glob pattern, relative to `${STEPUP_ROOT}`,
            with its trailing separator preserved when it is a directory pattern.
        subs
            The sub-pattern of each named wildcard, as given to `glob()`.
        paths
            Every match, sorted, directories with a trailing separator.
        """
        ng = NamedGlob(pattern, subs)
        ng.extend(paths)
        async with self.db:
            creator = self.scheduler.get_job_step(job_i)
            self.workflow.register_nglob(creator, ng)

    @allow_rpc
    async def define_step(
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
            creator = self.scheduler.get_job_step(job_i)
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
        # The new step may already be runnable, so wake up the scheduler to check for it.
        self.builder.wake_job_loop.set()

    @allow_rpc
    async def amend_step(
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
        this call blocks until they are resolved to `CONFIRMED` or `MISSING`,
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
            step = self.scheduler.get_job_step(job_i)
            unavailable, unfresh, to_check = self.workflow.amend_step(
                step,
                inp_paths=inp_paths,
                env_deps=env_deps,
                out_paths=out_paths,
                vol_paths=vol_paths,
                ran_concurrently=self.scheduler.ran_concurrently,
            )
        # The step is still running and may write the new outputs as soon as this call returns,
        # so their directories are created here rather than when the step was dispatched.
        self.workflow.create_dirs(Path(path).parent for path in chain(out_paths, vol_paths))
        if to_check:
            checked_paths = set(to_check)
            await self.builder.run_promoted_hash_jobs(to_check)
            async with self.db:
                for path in checked_paths:
                    file = self.workflow.find(File, path)
                    if file.get_state() not in (FileState.CONFIRMED, FileState.BUILT):
                        unavailable.add(path)
        carry_on = len(unavailable) == 0 and len(unfresh) == 0
        if not carry_on:
            self.executor.defer(job_i, unavailable=unavailable, unfresh=unfresh)
        return carry_on

    #
    # RPC from steps: dispatch, provenance and queries
    #

    @allow_rpc
    async def hold_dispatch(self, job_i: int) -> None:
        """Hold back this step's descendant steps from dispatch until a matching `release()`.

        Notes
        -----
        This is an RPC wrapper for `Step.hold`, which is re-entrant:
        nested `hold()` calls increment a counter,
        and descendants stay held back until the outermost `release()`.
        No job-loop wake-up is needed here: holding never creates new runnable work.
        """
        async with self.db:
            step = self.scheduler.get_job_step(job_i)
            step.hold()

    @allow_rpc
    async def release_dispatch(self, job_i: int) -> None:
        """Release one `hold()` on this step, decrementing its open-hold counter.

        Notes
        -----
        This is an RPC wrapper for `Step.release`.
        """
        async with self.db:
            step = self.scheduler.get_job_step(job_i)
            step.release()
        # Wake up the scheduler because previously held-back steps may now be runnable.
        self.builder.wake_job_loop.set()

    @allow_rpc
    async def record_subprocess(
        self,
        job_i: int,
        cmd: str,
        returncode: int,
        workdir: str,
        env_overrides: dict[str, str] | None,
        shell: bool,
        stdin: str,
        stdout: str,
        stderr: str,
    ) -> None:
        """Record a subprocess invocation made by a wrapper step.

        Notes
        -----
        This is an RPC wrapper for `Step.add_subprocess`,
        whose parameters are ordered as the columns of the `step_subprocess` table instead.
        The recorded metadata is informative for archival and debugging, not authoritative.
        """
        async with self.db:
            step = self.scheduler.get_job_step(job_i)
            step.add_subprocess(
                cmd=cmd,
                workdir=workdir,
                env_overrides=env_overrides,
                returncode=returncode,
                shell=shell,
                stdin=stdin,
                stdout=stdout,
                stderr=stderr,
            )

    @allow_rpc
    async def get_step_info(self, job_i: int) -> StepInfo:
        """Return step information, matching the return values of functions in `stepup.core.api`.

        For the sake of consistency, dynamic dependencies are not included.
        """
        async with self.db:
            step = self.scheduler.get_job_step(job_i)
            return step.get_info()

    #
    # RPC from steps or the user: diagnostics
    #

    @allow_rpc
    async def write_graph(self, prefix: str) -> None:
        """Write out the graph in text and dot formats."""
        async with self.db:
            with open(f"{prefix}.txt", "w", encoding="utf-8", newline="\n") as fh:
                print(self.workflow.format_str(), file=fh)
            with open(f"{prefix}_provenance.dot", "w", encoding="utf-8", newline="\n") as fh:
                print(self.workflow.format_dot_provenance(), file=fh)
            with open(f"{prefix}_dependency.dot", "w", encoding="utf-8", newline="\n") as fh:
                print(self.workflow.format_dot_dependency(), file=fh)
        await self.reporter(
            "DIRECTOR",
            f"Wrote graph to {prefix}.txt, {prefix}_provenance.dot and {prefix}_dependency.dot",
        )

    #
    # RPC from the user: phase control and shutdown
    #

    async def _wait_for_end_build_phase(self) -> None:
        """Block until the build phase ends (or the watch phase starts when using `--watch`)."""
        events = [self.stop_event]
        if self.watcher is not None:
            events.append(self.watcher.busy_watching)
        await wait_for_any_event(*events)

    async def _wait_for_change(self, path: str, observed: set[str]) -> None:
        """Block until `path` shows up in `observed`, a live set owned by the watcher.

        This also returns when the director is stopping,
        because a path that never changes would otherwise hold the call, and the connection
        serving it, until the process is killed.
        """
        path = Path(path).normpath()
        await self._wait_for_end_build_phase()
        with self.watcher.subscribe_changes() as event:
            while True:
                # The observation is checked before the stop event,
                # so a change that arrives together with the shutdown still counts.
                if path in observed or self.stop_event.is_set():
                    return
                await wait_for_any_event(event, self.stop_event)
                event.clear()

    @allow_rpc
    async def drain(self) -> None:
        """Stop dispatching new steps, leaving running steps to finish.

        This returns immediately, without waiting for the running steps:
        a caller that wants to wait uses `wait_for_idle` afterwards.
        When using the `--watch` option,
        StepUp switches to the watch phase when there are no more running steps.
        """
        self.scheduler.draining = True

    @allow_rpc
    async def wait_for_idle(self) -> None:
        """Block until the builder has completed all (runnable) steps."""
        await self._wait_for_end_build_phase()

    @allow_rpc
    async def wait_for_update(self, path: str) -> None:
        """Block until the watcher has observed an update of the file."""
        if self.watcher is not None:
            await self._wait_for_change(path, self.watcher.updated)

    @allow_rpc
    async def wait_for_delete(self, path: str) -> None:
        """Block until the watcher has observed the deletion of the file."""
        if self.watcher is not None:
            await self._wait_for_change(path, self.watcher.deleted)

    @allow_rpc
    async def start_build_phase(self, *, recheck: bool = False) -> None:
        """Leave the watch phase and build the steps made pending by the observed file changes.

        Parameters
        ----------
        recheck
            When True, `STEPUP_STARTUP_I` also gets a new value, as at a new start of the director,
            so that the steps that track it run again in the build phase that follows.
            When the director works offline, the value stays the same and a message says so.

        Notes
        -----
        This has no effect during the build phase, also not with `recheck=True`.
        """
        async with self._build_phase_lock:
            if self.watcher is None or not self.watcher.busy_watching.is_set():
                return
            if recheck:
                await update_startup_i(self.workflow, self.reporter, offline=self.offline)
                await rescan_env_vars(self.workflow, self.reporter)
            async with self.db:
                # Make all failed steps pending again for rerun.
                for step in self.workflow.steps(StepState.FAILED):
                    self.workflow.mark_step_pending(step)
            self.watcher.end_watching.set()
            # `shutdown` and `interrupt` end the watch phase without taking the lock,
            # and set `stop_event`, so this wait cannot hold the lock forever.
            await wait_for_any_event(self.watcher.done_watching, self.stop_event)
            self.scheduler.draining = False
            self.builder.resume.set()

    @allow_rpc
    async def wait_and_shutdown(self) -> None:
        """Block until the builder has completed all (runnable) steps, then shut down."""
        await self._wait_for_end_build_phase()
        await self.shutdown()

    @allow_rpc
    async def shutdown(self) -> None:
        """Shut down the director, escalating if called repeatedly.

        The first call drains the scheduler and stops the build/watch loops gracefully:
        steps already running are left to finish on their own.
        A second call interrupts running steps (`SIGINT` on POSIX);
        a third and any further call kills them (`SIGKILL`).

        Waiting for running steps is the point of this (deliberate, interactive) route,
        so it escalates only when asked to, never on a timer.
        A terminal signal takes the more abrupt `interrupt` route instead.
        """
        # Stop dispatching before anything else: the reporter calls below are real awaits,
        # during which the builder's job loop would otherwise still start new steps.
        # The stop event is what tells a first call from an escalating one,
        # so it must be sampled before `_stop_scheduling` sets it.
        escalating = self.stop_event.is_set()
        self._stop_scheduling()
        if escalating:
            level = self._next_stop_level
            await self.reporter(
                "DIRECTOR", f"Interrupting running steps ({describe_stop_level(level)})."
            )
            self.executor.interrupt(level)
            self._next_stop_level = StopLevel.KILL
        elif len(self.builder.running_tasks) > 0:
            await self.reporter("DIRECTOR", "Waiting for steps to complete before shutdown.")

    #
    # Signal handlers: abort and suspend
    #

    def _stop_scheduling(self) -> None:
        """Stop dispatching new work and end the watch phase, without touching running steps.

        Every effect is a set-only operation, so this is idempotent:
        a termination route may call it whether or not another one already did.
        Whether and how running steps are then signalled is the caller's policy.
        """
        self.scheduler.draining = True
        self.stop_event.set()
        if self.watcher is not None:
            self.watcher.end_watching.set()

    def interrupt(self, sig: signal.Signals) -> None:
        """Abort the build because a terminal signal was received.

        Unlike `shutdown` (the `q` key), this never waits for running steps:
        the user asked for everything to stop, so the build ends as soon as the steps do.
        The escalation to `StopLevel.KILL` is on a timer (`INTERRUPT_GRACE`)
        rather than on further signals,
        so that a single Ctrl-C is always enough to get the shell prompt back.
        A second signal only cuts that grace period short.

        This is a plain callback (not a coroutine), as a signal handler must be.
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
            "DIRECTOR",
            f"Aborting the build ({sig.name}). "
            f"Interrupting running steps ({describe_stop_level(StopLevel.INTERRUPT)}).",
        )
        self._stop_scheduling()
        # Steps are detached from the terminal, whose Ctrl-C does not reach them:
        # this is the only thing that stops them, on every route
        # (Ctrl-C, SIGTERM, or a SIGINT sent to the terminal user interface alone).
        self.executor.interrupt(StopLevel.INTERRUPT)
        # A `q` press after this must kill the steps, not interrupt them again.
        self._next_stop_level = StopLevel.KILL
        # Give the steps a moment to wind down on their own.
        # Polling keeps this simple
        # and lets shutdown proceed immediately once the last step is gone,
        # which is the common case.
        loop = asyncio.get_running_loop()
        deadline = loop.time() + INTERRUPT_GRACE
        while len(self.builder.running_tasks) > 0 and not self._interrupt_escalate.is_set():
            if loop.time() > deadline:
                break
            await asyncio.sleep(0.1)
        if len(self.builder.running_tasks) > 0:
            await self.reporter(
                "DIRECTOR",
                f"Killing unresponsive steps ({describe_stop_level(StopLevel.KILL)}).",
            )
            self.executor.interrupt(StopLevel.KILL)

    async def cancel_interrupt(self) -> None:
        """Cancel the pending grace period of `interrupt`, e.g. when the build ended in time."""
        if self._interrupt_task is not None and not self._interrupt_task.done():
            self._interrupt_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._interrupt_task
        self._interrupt_task = None

    def suspend(self) -> None:
        """Suspend the whole build after a `SIGTSTP`, and resume it when continued.

        Steps run in a session of their own, so the terminal never stops them:
        just like `interrupt`, this is the only thing that reaches them.
        Stopping this process is done by re-raising `SIGTSTP` with the default disposition,
        the canonical way for a program to honour a suspension, rather than by `SIGSTOP`:
        the shell tracks the terminal user interface as a job,
        and `fg` continues the whole process group, this process included.

        The steps are resumed in the `finally` block right after the self-stop,
        instead of from a `SIGCONT` handler, which keeps both halves in one readable block.
        It also degrades gracefully when this process group is orphaned (`setsid stepup build`):
        the kernel then discards the re-raised `SIGTSTP`, `os.kill` returns straight away
        and the steps are resumed immediately,
        matching the terminal user interface, which is not stopped in that situation either.

        Nothing is reported here:
        the reporter socket is served by the terminal user interface,
        which is stopping at this very moment,
        so awaiting it would block this process on its way down.
        The user-visible message is sent on resume instead.

        This is a plain callback (not a coroutine), as required by `add_signal_handler`.
        Blocking the event loop is the point:
        the process must not do anything else while it is suspended.
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
