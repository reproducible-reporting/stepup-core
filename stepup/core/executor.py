# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""In-process execution of steps.

The `Executor` runs each step directly inside the director's event loop as an asyncio task.
Launching the step's command (subprocess or forkserver child, see `stepup.core.run`)
and hashing its inputs/outputs (in a dedicated thread, see `stepup.core.hash`)
are delegated to their own modules;
the `Executor` ties the results into the step lifecycle.

A single `Executor` instance serves all concurrent steps.
Per-step mutable state lives in a `Run` created for each job.
"""

import asyncio
import functools
import multiprocessing
import os
import threading
import time
from collections.abc import Callable, Generator, Mapping
from contextlib import contextmanager
from typing import Any

import attrs
from path import Path

from .enums import FileState, HashUpdateCause, StepState
from .exceptions import HashCancelledError
from .file import File
from .hash import (
    FileHash,
    StepHash,
    compare_step_hashes,
    compute_both_hashes,
    compute_inp_hashes,
    compute_out_hashes,
)
from .hash_queue import HashJob
from .outcome import ChildOutcome, ResourceUsage
from .reporter import PROGRESS_REFRESH_DELAY, ReporterClient
from .run import Run, ThreadWorker, launch_command
from .scheduler import Scheduler
from .sqlite3 import DBSession
from .step import Step
from .utils import format_subprocess
from .workflow import Workflow

__all__ = ("Executor",)


#
# Helper class to block accidental overwrites in the dict of running jobs
#


class NoOverwriteDict(dict):
    """A dictionary that raises an error when a key is overwritten."""

    def __setitem__(self, key, value):
        if key in self:
            raise KeyError(f"Cannot overwrite key '{key}'. Keys in this dictionary are write-once.")
        super().__setitem__(key, value)


#
# Executor
#


@attrs.define
class Executor:
    """Run steps in the director process as asyncio tasks.

    One shared instance serves all concurrent steps.
    Its external API:

    - `validate_dynamic_job`, `try_skip_job` and `execute_job` are the coroutines
      created for each job.
    - `run_hash_job` is the coroutine created for each hash job.
    - `defer` marks a running job as deferred for later execution.
    - `interrupt` signals every currently running job.
    - `suspend` and `resume` stop and continue the running step commands.
    """

    # References to other StepUp components.

    scheduler: Scheduler = attrs.field(kw_only=True)
    """The scheduler that is managing the jobs."""

    workflow: Workflow = attrs.field(kw_only=True)
    """The workflow that the executor is interacting with."""

    db: DBSession = attrs.field(kw_only=True)
    """The workflow database session, i.e. the same object as `workflow.db`.

    It is used directly as an async context manager,
    which acquires exclusive access to the database for the duration of a transaction.
    """

    reporter: ReporterClient = attrs.field(kw_only=True)
    """A reporter to send progress and terminal output to."""

    mp_ctx: multiprocessing.context.BaseContext | None = attrs.field(kw_only=True, default=None)
    """Forkserver multiprocessing context, or `None` to use plain subprocesses."""

    # Boolean configuration flags

    explain_rerun: bool = attrs.field(kw_only=True)
    """Flag to explain why a step is rerun rather than skipped, or vice versa."""

    keep_going: bool = attrs.field(kw_only=True)
    """If `True`, do not drain the scheduler when a step fails."""

    live_progress: bool = attrs.field(kw_only=True)
    """Whether the reporter is an interactive terminal that wants live step-count updates."""

    write_joblog: bool = attrs.field(kw_only=True)
    """Whether to record `--joblog` events."""

    # Other configuration

    infra_env: dict = attrs.field(kw_only=True)
    """Environment variables from the director for step child processes, overriding `os.environ`."""

    # Internal state

    running: NoOverwriteDict[int, Run | HashJob] = attrs.field(init=False, factory=NoOverwriteDict)
    """The `Run`/`HashJob` instances currently in flight, keyed by `job_i`.

    `interrupt`, `suspend` and `resume` only read `.worker` from the values here,
    so a `HashJob` (which has no `.step`) can share this dict with `Run` without issue.
    `defer` does read step-specific attributes, but only ever gets the `job_i` of a step.
    """

    step_usage: ResourceUsage = attrs.field(init=False, factory=ResourceUsage)
    """The resource usage accumulated over all steps."""

    _base_env_cache: dict | None = attrs.field(init=False, default=None)
    """Cache for `base_env`, populated lazily on first access."""

    _counts_flush_handle: asyncio.TimerHandle | None = attrs.field(init=False, default=None)
    """Handle for the scheduled step-counts flush, or `None` when none is pending.

    Mirrors `ReporterClient._flush_jobs_handle`'s coalescing pattern.
    """

    _counts_flush_tasks: set[asyncio.Task] = attrs.field(init=False, factory=set)
    """In-flight step-counts flush tasks,
    kept alive here so they cannot be garbage-collected mid-send
    (same rationale as `ReporterClient._flush_tasks`)."""

    suspended_total: float = attrs.field(init=False, default=0.0)
    """Total wall time [s] spent suspended since the director started.

    `_run_command` samples this before and after launching a child process,
    so that time spent suspended is not counted as the step's wall time.
    """

    _suspend_start: float | None = attrs.field(init=False, default=None)
    """`time.perf_counter()` when `suspend` stopped the steps, or `None` when not suspended."""

    #
    # Cached properties
    #

    @property
    def base_env(self) -> dict:
        """The base environment for step child processes: `os.environ` with `infra_env` applied.

        No copy is returned, so the caller must not mutate the returned dictionary.
        """
        if self._base_env_cache is None:
            self._base_env_cache = {**os.environ, **self.infra_env}
        return self._base_env_cache

    #
    # External control entry points
    #

    def defer(
        self, job_i: int, *, unavailable: set[str] | None = None, unfresh: set[str] | None = None
    ):
        """Mark a step as deferred for later execution due to unavailable or unfresh inputs."""
        run = self.running.get(job_i)
        if run is None:
            raise ValueError(f"No running step found for job_i={job_i}.")
        if unavailable is not None:
            run.unavailable.update(unavailable)
        if unfresh is not None:
            run.unfresh.update(unfresh)
        run.success = False

    def interrupt(self, sig: int):
        """Send a signal to all currently running step commands, or cancel a running hash."""
        for run in list(self.running.values()):
            if run.worker is not None:
                run.worker.interrupt(sig)

    def suspend(self) -> None:
        """Stop the child process of every running step, for the duration of a suspension.

        This is not a variation on `interrupt`:
        `ThreadWorker.interrupt` cancels its computation instead of signalling a process,
        which is the opposite of what a suspension needs.
        Hash threads are left alone here, see `ThreadWorker.suspend`.
        """
        if self._suspend_start is not None:
            return
        self._suspend_start = time.perf_counter()
        for run in list(self.running.values()):
            if run.worker is not None:
                run.worker.suspend()

    def resume(self) -> tuple[int, float]:
        """Continue what `suspend` stopped.

        The director's event loop is frozen between `suspend` and `resume`,
        so `running` holds exactly the same jobs in both.

        Returns
        -------
        nrun
            The number of running jobs that were resumed.
        seconds
            The wall time spent suspended.
        """
        if self._suspend_start is None:
            return 0, 0.0
        seconds = time.perf_counter() - self._suspend_start
        self._suspend_start = None
        self.suspended_total += seconds
        nrun = 0
        for run in list(self.running.values()):
            if run.worker is not None:
                run.worker.resume()
                nrun += 1
        return nrun, seconds

    #
    # Functions called by jobs, see job.py
    #

    async def validate_dynamic_job(
        self,
        job_i: int,
        step: Step,
        inp_hashes: Mapping[str, FileHash],
        env_deps: list[str],
        step_hash: StepHash,
    ):
        """Test whether the input hashes changed, which would invalidate the dynamic step info.

        If the dynamic step info is still valid, the step is put back in the pending state,
        so that it can be re-queued when new inputs arrive.
        """
        run, new_hash = await self._new_run(job_i, step, inp_hashes, env_deps)
        if new_hash is None:
            # Step failed early due to unexpected input changes, error already reported.
            return
        if step_hash.inp_digest != new_hash.inp_digest:
            # Inputs have changed, so discard dynamic info.
            await self._outdated_dynamic(run, step_hash, new_hash)
            await self._reset_step_to_pending(step)
            return

        # If we get here, no relevant inputs have changed,
        # so we can make the step pending again, to be re-queued when new inputs arrive.
        async with self.db:
            step.set_state(StepState.PENDING)
        self._report_step_counts()

    async def try_skip_job(
        self,
        job_i: int,
        step: Step,
        inp_hashes: Mapping[str, FileHash],
        env_deps: list[str],
        step_hash: StepHash,
    ):
        """Try skipping a step."""
        run, new_hash = await self._new_run(job_i, step, inp_hashes, env_deps)
        if new_hash is None:
            # Step failed early due to unexpected input changes, error already reported.
            return

        if step_hash.inp_digest != new_hash.inp_digest:
            # The inputs have changed, so must run.
            await self._noskip(run, step_hash, new_hash)
            await self._reset_step_to_pending(step)
            return

        # Compute the output part of the step hash.
        new_hash, all_out_hashes = await self._compute_out_step_hash(run, new_hash)
        if new_hash is None:
            # Hash computation was cancelled because the build is shutting down.
            # Report the failure now.
            await self._finalize_failed_run(run)
            return

        if step_hash.out_digest != new_hash.out_digest:
            # The outputs have changed, so must run.
            await self._noskip(run, step_hash, new_hash)
            await self._reset_step_to_pending(step)
            # The output files must have been changed externally.
            # The new file hashes of the outputs are not stored,
            # to ensure that they are not deleted in a cleanup phase.
            return

        # All checks passed: no need to run the step, just simulate the products.
        await self._skip(run, step_hash)
        out_cause = HashUpdateCause.SUCCEEDED if run.success else HashUpdateCause.FAILED
        async with self.db:
            # A skipped step must still record its outputs:
            # this update is what settles their state,
            # and it also refreshes a stored hash that went stale,
            # e.g. when the user restored an output to the content the step produces.
            self.workflow.update_file_hashes(all_out_hashes, cause=out_cause)
            step.mark_completed(new_hash, False)
            # Do not call `scheduler.record_run_stopped`, as no start time was recorded either.
        self._report_step_counts()

    async def execute_job(
        self, job_i: int, step: Step, inp_hashes: Mapping[str, FileHash], env_deps: list[str]
    ):
        """Execute a step (no skipping).

        If it wants to be deferred (unavailable or unfresh dynamic inputs)
        but the defer cap has been exceeded,
        the step fails instead of being scheduled for another execution attempt.
        """
        self.scheduler.record_run_started(step.i)
        run, new_hash = await self._new_run(job_i, step, inp_hashes, env_deps)
        if new_hash is None:
            # Step failed early due to unexpected input changes, error already reported.
            return

        # Run the step
        async with self.db:
            step.reset_for_rerun()
        self._report_step_counts()
        await self._run_command(run)

        # Recompute the step hash (inputs and outputs).
        # Hashes are always updated, even for failed commands,
        # so outputs can be removed safely if they are no longer needed.
        new_hash, new_inp_hashes, all_out_hashes = await self._compute_full_step_hash(run)
        unexpected_input_changes = len(new_inp_hashes) > 0

        async with self.db:
            new_hash, wants_defer = self._classify_execution(
                run, new_hash, new_inp_hashes, unexpected_input_changes
            )
            out_cause = HashUpdateCause.SUCCEEDED if run.success else HashUpdateCause.FAILED
            self.workflow.update_file_hashes(all_out_hashes, cause=out_cause)
            run.interrupted_defer = step.mark_completed(new_hash, wants_defer)
            self.scheduler.record_run_stopped(step.i, succeeded=new_hash is not None)
            if wants_defer and not run.interrupted_defer:
                # Erase error info to keep the screen output concise.
                run.outcome = None
            if run.outcome is not None:
                # Persist the captured output in the same transaction as `mark_completed()`,
                # so a crash cannot leave a completed step without its output (or vice versa).
                # `run.outcome` itself stays untruncated,
                # because `set_outcome` only truncates what it writes to the database,
                # so `_report_run` below still forwards the full text to the TUI.
                step.set_outcome(run.outcome)
        self._report_step_counts()

        # Report the result of running the step
        await self._report_run(run)

        if unexpected_input_changes:
            # Changes to inputs are suspect and can break everything.
            # End the build phase gracefully by draining the scheduler.
            await self._drain_for_unexpected_input_changes()

    #
    # Job function helper methods
    #

    def _classify_execution(
        self,
        run: Run,
        new_hash: StepHash | None,
        new_inp_hashes: Mapping[str, FileHash],
        unexpected_input_changes: bool,
    ) -> tuple[StepHash | None, bool]:
        """Decide success/failure/defer for a just-executed step, updating `run` in place.

        Must be called inside the database transaction that persists the step's completion,
        since it calls `update_file_hashes` when inputs changed unexpectedly.

        Parameters
        ----------
        run
            The per-step state object.
            Its `success`, `unavailable` and `unfresh` attributes may be updated in place.
        new_hash
            The step hash computed by `_compute_full_step_hash`, before classification.
        new_inp_hashes
            The input file hashes that differed from what was expected before the step ran.
        unexpected_input_changes
            Whether `new_inp_hashes` is non-empty.

        Returns
        -------
        new_hash
            The step hash to record, or `None` if the step must not be marked succeeded.
        wants_defer
            Whether the step should be deferred instead of completed.
        """
        wants_defer = len(run.unavailable) > 0 or len(run.unfresh) > 0

        if unexpected_input_changes:
            # This is the worst case: inputs should never change while a step is running.
            # If they do, fail hard and stop ASAP.
            # The rest of the stopping logic runs after this classification, in `execute_job()`.
            run.success = False
            new_hash = None
            # Clear the dynamic inputs to mark the step as failed instead of pending.
            run.unavailable.clear()
            run.unfresh.clear()
            wants_defer = False
            self.workflow.update_file_hashes(new_inp_hashes, cause=HashUpdateCause.OBSERVED)
        elif wants_defer:
            # Rescheduling in the `mark_completed()`` method needs the new hash to be None,
            # so the step is not marked as succeeded.
            run.success = False
            new_hash = None
        elif not run.success:
            # Some other failure occurred (command failed, output missing, etc.)
            new_hash = None

        return new_hash, wants_defer

    def _report_step_counts(self) -> None:
        """Request a step-state counts report, coalescing with any already pending.

        This is a no-op when the reporter has no live progress display to feed
        (`live_progress` is `False`).
        Otherwise, it schedules `_flush_step_counts` `PROGRESS_REFRESH_DELAY` from now,
        unless a flush is already pending,
        so that a burst of calls (e.g. several steps completing in the same event-loop iteration)
        collapses into a single `count_required_steps()` scan and RPC call.

        Mirrors `ReporterClient._request_jobs_flush`'s coalescing timer.
        """
        if not self.live_progress or self._counts_flush_handle is not None:
            return
        loop = asyncio.get_running_loop()
        self._counts_flush_handle = loop.call_later(
            PROGRESS_REFRESH_DELAY, self._on_counts_flush_timer
        )

    def _on_counts_flush_timer(self) -> None:
        self._counts_flush_handle = None
        task = asyncio.get_running_loop().create_task(self._flush_step_counts())
        self._counts_flush_tasks.add(task)
        task.add_done_callback(self._counts_flush_tasks.discard)

    async def _flush_step_counts(self) -> None:
        """Send the current step-state counts to the reporter."""
        async with self.db:
            nsuccess, ntotal = self.workflow.count_required_steps()
        await self.reporter.update_progress(nsuccess, ntotal)

    async def _reset_step_to_pending(self, step: Step) -> None:
        """Discard a step's stored hash and transition it back to `PENDING` for re-execution."""
        async with self.db:
            step.reset_for_rerun()
            step.delete_hash()
            step.set_state(StepState.PENDING)

    async def _new_run(
        self,
        job_i: int,
        step: Step,
        inp_hashes: Mapping[str, FileHash],
        env_deps: list[str],
    ) -> tuple[Run, StepHash | None]:
        """Set up a fresh `Run` and compute the input part of its step hash.

        Parameters
        ----------
        job_i
            Unique id of this run attempt, assigned by `Scheduler` when the job was created.
        step
            The step being executed.
        inp_hashes
            The input file hashes for the step, keyed by path.
        env_deps
            The names of environment variables that the step depends on.

        Returns
        -------
        run
            The per-step state object.
        new_step_hash
            The new hash of the step, with the input part already computed, if available.
            `None` if, unexpectedly, some inputs are missing or have changed.
        """
        run = Run(step, job_i=job_i)
        new_step_hash, new_inp_hashes = await self._compute_inp_step_hash(run, inp_hashes, env_deps)
        if new_step_hash is not None:
            return run, new_step_hash

        # Either the hash computation was cancelled because the build is shutting down
        # (see `Executor.interrupt`),
        # in which case `new_inp_hashes` is empty
        # and `run` has already been marked failed by `_run_work_thread`,
        # or the hashes of the input files on disk differ from those in the database,
        # or some inputs were deleted.
        # The latter two break the workflow,
        # so the step is flagged as failed and the scheduler held.
        unexpected_input_changes = len(new_inp_hashes) > 0
        if unexpected_input_changes:
            async with self.db:
                self.workflow.update_file_hashes(new_inp_hashes, cause=HashUpdateCause.OBSERVED)
        await self._finalize_failed_run(run)
        if unexpected_input_changes:
            await self._drain_for_unexpected_input_changes()
        return run, None

    async def _finalize_failed_run(self, run: Run) -> None:
        """Complete, record and report a run that failed before producing a new step hash."""
        async with self.db:
            run.step.mark_completed(None, False)
        self.scheduler.record_run_stopped(run.step.i, succeeded=False)
        self._report_step_counts()
        await self._report_run(run)

    async def _drain_for_unexpected_input_changes(self) -> None:
        """Drain the scheduler because a step's inputs changed unexpectedly."""
        self.scheduler.draining = True
        await self.reporter("ERROR", "The scheduler is draining due to unexpected input changes.")

    #
    # Hash computation helpers
    #

    async def _run_work_thread(self, run: Run, work: Callable[[threading.Event], Any]) -> Any:
        """Run a GIL-releasing computation in a thread.

        Returns `None` if the computation was cancelled by an interrupted shutdown
        (see `Executor.interrupt`) or failed outright,
        in which case `run` has already been marked failed.
        """
        with self._track_running(run):
            worker = ThreadWorker(work=work, job_i=run.job_i)
            run.worker = worker
            try:
                return await worker.run_in_thread()
            except HashCancelledError:
                self._fail_run_with_message(
                    run, "Hash computation was cancelled because the build is shutting down."
                )
                return None
            except Exception as exc:  # noqa: BLE001
                # Must not propagate as a task exception:
                # that would crash `job_loop` via `handle_done_tasks`,
                # tearing down the whole director over one bad file
                # (e.g. `HashFailedError` when a step's output turned out to be a directory,
                # or a `PermissionError` from `stat()`).
                # Treat it as this step's own failure instead,
                # mirroring `_run_hash_job`'s handling of the same class of error.
                self._fail_run_with_message(run, f"Hash computation failed: {exc}")
                return None
            finally:
                run.worker = None

    @staticmethod
    def _fail_run_with_message(run: Run, message: str) -> None:
        """Mark `run` as failed, appending `message` to its outcome's stderr."""
        run.success = False
        if run.outcome is None:
            run.outcome = ChildOutcome(1, "", message)
        else:
            stderr = run.outcome.stderr
            stderr += "\n" if run.outcome.stderr else ""
            stderr += message
            run.outcome = attrs.evolve(run.outcome, stderr=stderr)

    @contextmanager
    def _track_running(self, job: Run | HashJob) -> Generator[None, None, None]:
        """Track a `Run` or `HashJob` as running for the duration of the context."""
        if job.job_i in self.running:
            raise RuntimeError(f"Job {job.job_i} is already tracked as running.")
        self.running[job.job_i] = job
        try:
            yield
        finally:
            del self.running[job.job_i]

    async def run_hash_job(self, hash_job: HashJob) -> None:
        """Run `hash_job`, bracketed by progress-bar start/stop calls.

        The bracket lives here, rather than at the places where a hash job is started,
        so a hash job is equally visible however it got claimed.

        `"H"` is its letter in the progress bar, and `HashJob.job_i` is negative
        (see `hash_queue.py`), so it can never collide with a real `Step.i` in the
        reporter/progress-bar dict, which is keyed by whatever int it is given.
        """
        self.reporter.job_started(hash_job.job_i, "H", hash_job.path)
        try:
            await self._run_hash_job(hash_job)
        finally:
            self.reporter.job_stopped(hash_job.job_i)

    async def _run_hash_job(self, hash_job: HashJob) -> None:
        """Compute one file hash in a thread and resolve the future with it.

        Applying the result to the workflow is the awaiter's job, not this method's.
        See the `hash_queue.py` module docstring.

        Does not reuse `_run_work_thread`: that helper requires a `Run` (step-bound) and
        writes a child outcome into `run.outcome`, neither of which applies to a `HashJob`.
        """
        worker = ThreadWorker(
            work=functools.partial(FileHash.refreshed, hash_job.old_hash, hash_job.path),
            job_i=hash_job.job_i,
        )
        hash_job.worker = worker
        with self._track_running(hash_job):
            try:
                new_hash = await worker.run_in_thread()
            except HashCancelledError:
                hash_job.future.cancel()
                await self.reporter("ERROR", f"Hash cancelled for {hash_job.path}")
                return
            except Exception as exc:  # noqa: BLE001
                # E.g. a `PermissionError` from `stat()`,
                # or a directory mistakenly used as a file input.
                # Must not propagate as a task exception:
                # that would crash `job_loop` via `handle_done_tasks`,
                # tearing down the whole director over one bad file.
                # Instead, borrow `draining`'s existing visibility and effect:
                # stop dispatching new steps and report the error loudly
                # (mirroring `handle_done_tasks`' own `draining`
                # and `report_unbuilt`'s "Scheduler is draining" warning),
                # while letting already-running or queued work wind down normally.
                # The file is left UNCONFIRMED.
                # A fire-and-forget submitter never awaits this future,
                # so draining and the reported error are what surface the failure to the user.
                self.scheduler.draining = True
                hash_job.future.set_exception(exc)
                await self.reporter(
                    "ERROR",
                    f"Could not hash {hash_job.path}: {exc}",
                    await self._format_provenance(hash_job.path),
                )
                return
            finally:
                hash_job.worker = None
        if not hash_job.future.done():
            # Already done when the future was cancelled concurrently (e.g. Builder.stop());
            # set_result would then raise InvalidStateError.
            hash_job.future.set_result(new_hash)

    async def _format_provenance(self, path: str) -> list[tuple[str, str]]:
        """Format where `path` came from in the workflow, as a reporter page.

        A failing hash job carries no step context of its own: it is bookkeeping for a file,
        not a step, so `Could not hash <path>` alone leaves the user guessing which line of
        which `plan.py` put that path in the workflow. This page fills that gap with the node
        that created the file and the steps that consume it, each followed by the step that
        declared it, which is usually the `plan.py` holding the offending call.

        Returns
        -------
        pages
            A single `(title, body)` page, or no page at all when the workflow has no record
            of `path`, e.g. when the file node was removed while the hash was running.
        """
        role_column = 20
        async with self.db:
            file = self.workflow.find(File, path)
            if file is None:
                return []
            creator = file.creator()
            related = [] if creator is None else [("creator", creator)]
            related.extend(
                ("sink", node) for node in sorted(file.sinks(Step), key=lambda node: node.label)
            )
            lines = []
            for role, node in related:
                lines.append(f"{role:>{role_column}s}   {node.key()}")
                declarer = node.creator()
                # Only a step declarer is worth a line: it names the script to fix.
                # A non-step creator (the root node) adds nothing the user can act on.
                if isinstance(declarer, Step):
                    lines.append(f"{'declared by':>{role_column}s}   {declarer.key()}")
        return [(f"Provenance of {path}", "\n".join(lines))]

    async def _compute_inp_step_hash(
        self,
        run: Run,
        inp_hashes: Mapping[str, FileHash],
        env_deps: list[str],
    ) -> tuple[StepHash | None, dict[str, FileHash]]:
        """Compute the input part of a step hash and apply it to `run`."""
        result = await self._run_work_thread(run, functools.partial(compute_inp_hashes, inp_hashes))
        if result is None:
            return None, {}

        # If there are unexpected issues with inputs, bail out.
        if len(result.messages) > 0:
            run.inp_messages.extend(result.messages)
            run.success = False
            return None, result.new_hashes

        # Get some info from the workflow to include in the step hash.
        async with self.db:
            shell = run.step.uses_shell()
            env_overrides = run.step.get_env_overrides()

        step_hash = StepHash.from_inp(
            run.step.label,
            result.all_hashes,
            {name: self.base_env.get(name) for name in env_deps},
            explained=self.explain_rerun,
            shell=shell,
            env_overrides=env_overrides,
        )
        run.inp_digest = step_hash.inp_digest
        return step_hash, {}

    async def _compute_out_step_hash(
        self, run: Run, step_hash: StepHash
    ) -> tuple[StepHash | None, dict[str, FileHash]]:
        """Compute the output part of a step hash and apply it to `run`.

        Returns
        -------
        step_hash
            `None` if the hash computation was cancelled by an interrupted shutdown.
        all_out_hashes
            The freshly computed hashes of all outputs of the step.
        """
        async with self.db:
            out_hashes = {rec.path: rec.hash for rec in run.step.out_paths()}

        result = await self._run_work_thread(run, functools.partial(compute_out_hashes, out_hashes))
        if result is None:
            return None, {}

        if len(result.messages) > 0:
            run.out_missing.extend(result.messages)
            run.success = False
        step_hash = step_hash.with_out_hashes(result.all_hashes)

        return step_hash, result.all_hashes

    async def _compute_full_step_hash(
        self, run: Run
    ) -> tuple[StepHash | None, dict[str, FileHash], dict[str, FileHash]]:
        """Compute a new step hash with updated input and output file hashes, applied to `run`."""
        async with self.db:
            # Some inputs may be dynamic and still unavailable,
            # for which checking hashes is too early.
            # Therefore, only check the hashes of built and confirmed files.
            inp_hashes = {
                rec.path: rec.hash
                for rec in run.step.inp_paths()
                if rec.state in (FileState.BUILT, FileState.CONFIRMED)
            }
            env_deps = list(run.step.env_deps())
            out_hashes = {rec.path: rec.hash for rec in run.step.out_paths()}
            shell = run.step.uses_shell()
            env_overrides = run.step.get_env_overrides()

        result = await self._run_work_thread(
            run, functools.partial(compute_both_hashes, inp_hashes, out_hashes)
        )
        if result is None:
            return None, {}, {}

        inp_result, out_result = result

        if len(inp_result.messages) == 0:
            step_hash = StepHash.from_inp(
                run.step.label,
                inp_result.all_hashes,
                {name: self.base_env.get(name) for name in env_deps},
                explained=self.explain_rerun,
                shell=shell,
                env_overrides=env_overrides,
            )
            step_hash = step_hash.with_out_hashes(out_result.all_hashes)
        else:
            step_hash = None
            run.inp_messages.extend(inp_result.messages)
            run.success = False
        if len(out_result.messages) > 0:
            run.out_missing.extend(out_result.messages)
            run.success = False

        return step_hash, inp_result.new_hashes, out_result.all_hashes

    #
    # Command execution helper
    #

    async def _run_command(self, run: Run):
        """Run the command of the step described by `run`."""
        await self.reporter("START", run.description)

        command, workdir = run.step.command_and_workdir
        async with self.db:
            shell = run.step.uses_shell()
            need = run.step.get_need()
            env_overrides = run.step.get_env_overrides()
            out_paths = [record.path for record in run.step.out_paths()]
            vol_paths = [record.path for record in run.step.vol_paths()]

        # The step is about to run in its working directory and to write its outputs,
        # which is the moment these directories are needed.
        # Outputs declared later are handled by the `amend_step` RPC.
        self.workflow.create_dirs([workdir, *(Path(path).parent for path in out_paths + vol_paths)])

        env = dict(self.base_env)
        # Apply step-specific overrides first, so the reserved variables below always win.
        env.update(env_overrides)
        # For internal use in command:
        env["STEPUP_JOB_I"] = str(run.job_i)
        # Client code may use the following:
        env["STEPUP_STEP_INP_DIGEST"] = run.inp_digest.hex()
        env["STEPUP_STEP_NEED"] = need.name
        env["ROOT"] = str(Path.cwd().relpath(workdir))
        env["HERE"] = str(Path(workdir).relpath())
        # Note: the variables defined here must be listed in `RESERVED_ENV_VARS`.

        suspended_before = self.suspended_total
        with self._track_running(run):
            outcome = await launch_command(
                command, shell=shell, env=env, cwd=workdir, mp_ctx=self.mp_ctx, run=run
            )
        # A suspension stops the child but not the monotonic clock, on either launch path,
        # so it would otherwise be recorded as time the step spent working.
        # This assumes a stopped step makes no progress,
        # which holds for work that needs the CPU.
        # A step waiting on a timer is the exception:
        # the kernel keeps its deadline running while it is stopped,
        # so part of the discounted time was progress after all.
        suspended = self.suspended_total - suspended_before
        if suspended > 0.0:
            usage = attrs.evolve(outcome.usage, wtime=max(0.0, outcome.usage.wtime - suspended))
            outcome = attrs.evolve(outcome, usage=usage)

        self.step_usage += outcome.usage
        run.outcome = outcome
        if run.outcome.returncode != 0:
            run.success = False

    #
    # Reporting helpers
    #

    async def _report_run(self, run: Run):
        """Report the result of a step's execution."""
        pages = await self._build_report_pages(run)
        tag = self._determine_tag(run)
        if tag == "FAIL" and not self.keep_going:
            self.scheduler.draining = True
        await self.reporter(tag, run.description, pages)

    async def _build_report_pages(self, run: Run) -> list[tuple[str, str]]:
        """Build the report pages describing what happened during a step's execution."""
        command, workdir = run.step.command_and_workdir
        pages = []
        needs_defer = not (
            (len(run.unavailable) == 0 and len(run.unfresh) == 0) or run.interrupted_defer
        )
        if not (run.success or needs_defer):
            # Format the command for display, so it can be copied and pasted into a shell.
            # A non-zero return code is appended
            # as a trailing `# exit=N` comment by `format_subprocess`.
            async with self.db:
                shell = run.step.uses_shell()
            pages.append(
                (
                    f"Deferred more than {self.workflow.defer_cap} times"
                    if run.interrupted_defer
                    else "Failed command",
                    format_subprocess(
                        command,
                        workdir,
                        returncode=None if run.outcome is None else run.outcome.returncode,
                        shell=shell,
                    ),
                )
            )
        if len(run.unavailable) > 0:
            pages.append(("Unavailable dynamic inputs", "\n".join(sorted(run.unavailable))))
        if len(run.unfresh) > 0:
            pages.append(("Unfresh dynamic inputs", "\n".join(sorted(run.unfresh))))
        if len(run.inp_messages) > 0:
            run.inp_messages.sort()
            pages.append(("Invalid inputs", "\n".join(run.inp_messages)))
        if not needs_defer and len(run.out_missing) > 0:
            # Do not show missing outputs, as they are fairly normal and harmless when deferring.
            run.out_missing.sort()
            pages.append(("Expected outputs not created", "\n".join(run.out_missing)))
        if run.outcome is not None:
            stdout = run.outcome.stdout.rstrip()
            if len(stdout) > 0:
                pages.append(("Standard output", stdout))
            stderr = run.outcome.stderr.rstrip()
            if len(stderr) > 0:
                pages.append(("Standard error", stderr))
        return pages

    def _determine_tag(self, run: Run) -> str:
        """Derive the reporter tag (`SUCCESS`, `FAIL`, ...) for a finished step."""
        if run.interrupted_defer:
            return "FAIL"
        if len(run.unavailable) > 0 or len(run.unfresh) > 0:
            return "DEFERRED"
        if run.success:
            return "SUCCESS"
        return "FAIL"

    async def _skip(self, run: Run, step_hash: StepHash):
        """Report a skipped step."""
        pages = []
        if self.explain_rerun:
            page_change, page_same = compare_step_hashes(step_hash, step_hash)
            if len(page_change) > 0:
                raise AssertionError(
                    "A skipped step cannot have changes in inputs, env vars or outputs."
                )
            if len(page_same) > 0:
                pages.append(("No changes observed", page_same))
        await self.reporter("SKIP", run.description, pages)

    async def _noskip(self, run: Run, old_hash: StepHash, new_hash: StepHash):
        """Report a step that was not skipped."""
        if self.explain_rerun:
            pages = []
            if len(run.out_missing) > 0:
                pages.append(("Missing output files", "\n".join(run.out_missing)))
            page_change, page_same = compare_step_hashes(old_hash, new_hash)
            if len(page_change) > 0:
                pages.append(("Changes causing rerun", page_change))
            if len(page_same) > 0:
                pages.append(("Remained the same", page_same))
            await self.reporter("NOSKIP", run.description, pages)

    async def _outdated_dynamic(self, run: Run, old_hash: StepHash, new_hash: StepHash):
        """Report a step whose dynamic inputs have changed."""
        if self.explain_rerun:
            page_change, page_same = compare_step_hashes(old_hash, new_hash)
            pages = [("Outdated dynamic dependencies", page_change)]
            if len(page_same) > 0:
                pages.append(("Remained the same (or missing)", page_same))
            await self.reporter("DROPAMEND", run.description, pages)
