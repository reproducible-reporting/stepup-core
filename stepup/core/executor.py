# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""In-process execution of steps.

The `Executor` runs each step directly inside the director's event loop as an asyncio task.
Launching the step's command (subprocess or forkserver child, see `stepup.core.run`) and
hashing its inputs/outputs (in a dedicated thread, see `stepup.core.hash`) are delegated to
their own modules; the `Executor` ties the results into the step lifecycle.

A single `Executor` instance serves all concurrent steps.
A per-step mutable state lives in a `Run` created for each job.
"""

import functools
import multiprocessing
import os
import threading
from collections.abc import Callable
from contextlib import contextmanager
from time import perf_counter
from typing import Any

import attrs
from path import Path

from .enums import FileState, HashUpdateCause, StepState
from .exceptions import HashCancelledError
from .hash import (
    FileHash,
    StepHash,
    compare_step_hashes,
    compute_both_hashes,
    compute_inp_hashes,
    compute_out_hashes,
)
from .reporter import ReporterClient
from .run import Run, ThreadWorker, launch_command
from .scheduler import Scheduler
from .sqlite3 import DBSession
from .step import Step
from .usage import ResourceAccumulator
from .utils import format_subprocess
from .workflow import Workflow

__all__ = ("Executor",)


#
# Helper class to block accidental overwrites in the running steps dict
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


STEP_COUNTS_REPORT_INTERVAL = 0.5
"""Minimum time (seconds) between `get_step_counts()` reports sent to the reporter.

Reporting more often than this would only add `get_step_counts()` cost
(a full `step` table scan) without any visible benefit.
"""


@attrs.define
class Executor:
    """Run steps in the director process as asyncio tasks.

    One shared instance serves all concurrent steps.
    The external API is used as follows:

    - The methods `validate_amended_job`, `try_skip_job` and `execute_job`
      are the coroutines created by the builder for each job.
    - `postpone` is called from `director.py`
    - `interrupt` is called from `builder.py`.
    """

    # References to other StepUp components.

    scheduler: Scheduler = attrs.field(kw_only=True)
    """The scheduler that is managing the jobs."""

    workflow: Workflow = attrs.field(kw_only=True)
    """The workflow that the executor is interacting with."""

    db: DBSession = attrs.field(kw_only=True)
    """Lock for workflow database access."""

    reporter: ReporterClient = attrs.field(kw_only=True)
    """A reporter to send progress and terminal output to."""

    mp_ctx: multiprocessing.context.BaseContext | None = attrs.field(kw_only=True, default=None)
    """Forkserver multiprocessing context, or None to use plain subprocesses."""

    # Boolean configuration flags

    show_perf: bool = attrs.field(kw_only=True)
    """Flag to enable detailed CPU usage of each step."""

    explain_rerun: bool = attrs.field(kw_only=True)
    """Flag to explain why a step is rerun rather than skipped, or vice versa."""

    keep_going: bool = attrs.field(kw_only=True)
    """If True, do not put the scheduler on hold when a step fails."""

    live_progress: bool = attrs.field(kw_only=True)
    """Whether the reporter is an interactive terminal that wants live step-count updates."""

    do_joblog: bool = attrs.field(kw_only=True)
    """Whether to record `--joblog` events."""

    # Other configuration

    infra_env: dict = attrs.field(kw_only=True)
    """Environment variables from the director for step child processes, overriding `os.environ`."""

    # Internal state

    running: NoOverwriteDict[int, Run] = attrs.field(init=False, factory=NoOverwriteDict)
    """The `Run` instances whose command is currently running, keyed by `Run.job_i`."""

    step_accumulator: ResourceAccumulator = attrs.field(init=False, factory=ResourceAccumulator)
    """Running totals of CPU time and block-IO op counts for steps."""

    _base_env_cache: dict | None = attrs.field(init=False, default=None)
    """Cache for `base_env`, populated lazily on first access."""

    _last_step_counts_time: float | None = attrs.field(init=False, default=None)
    """`perf_counter()` timestamp of the last step-counts report, or `None` before the first."""

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

    def postpone(
        self, job_i: int, *, unavailable: set[str] | None = None, unfresh: set[str] | None = None
    ):
        """Mark a step as postponed for later execution due to unavailable or unfresh inputs."""
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

    #
    # Functions called by jobs, see job.py
    #

    async def validate_amended_job(
        self,
        job_i: int,
        step: Step,
        inp_hashes: list[tuple[str, FileHash]],
        env_deps: list[str],
        step_hash: StepHash,
    ):
        """Test if the inputs (hashes) have changed, which would invalidate the amended step info.

        If the job can be validated, it is put back in the pending state,
        so that it can be re-queued when new inputs arrive.
        """
        run, new_hash = await self._new_run(job_i, step, inp_hashes, env_deps)
        if new_hash is None:
            # Step failed early due to unexpected input changes, error already reported.
            return
        if step_hash.inp_digest != new_hash.inp_digest:
            # Inputs have changed, so discard amended info
            await self._outdated_amended(run, step_hash, new_hash)
            await self._reset_step_to_pending(step)
            return

        # If we get here, no relevant inputs have changed,
        # so we can make the step pending again, to be re-queued when new inputs arrive.
        async with self.db:
            step.set_state(StepState.PENDING)
        await self._report_step_counts()

    async def try_skip_job(
        self,
        job_i: int,
        step: Step,
        inp_hashes: list[tuple[str, FileHash]],
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
        new_hash, new_out_hashes = await self._compute_out_step_hash(run, new_hash)
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
        async with self.db:
            # If output hashes changed fortuitously,
            # e.g. the user restored them to the expected state,
            # we still want to record the new hash.
            self.workflow.update_file_hashes(new_out_hashes, HashUpdateCause.SUCCEEDED)
            step.completed(new_hash, False)
            # Do not call `scheduler.record_stop_time`, as no start time was recorded either.
        await self._report_step_counts()

    async def execute_job(
        self, job_i: int, step: Step, inp_hashes: list[tuple[str, FileHash]], env_deps: list[str]
    ):
        """Execute a step (no skipping).

        The command always runs. If it wants to be postponed (unavailable or unfresh
        amended inputs) but the postpone cap has been exceeded, the step is marked as
        failed instead of being scheduled for another execution attempt.
        """
        run, new_hash = await self._new_run(job_i, step, inp_hashes, env_deps)
        if new_hash is None:
            # Step failed early due to unexpected input changes, error already reported.
            return

        # Run the step
        async with self.db:
            step.reset_for_rerun()
        await self._report_step_counts()
        await self._run_command(run)

        # Recompute the step hash (inputs and outputs).
        # Hashes are always updated, even for failed commands,
        # so outputs can be removed safely if they are no longer needed.
        new_hash, new_inp_hashes, new_out_hashes = await self._compute_full_step_hash(run)
        unexpected_input_changes = len(new_inp_hashes) > 0

        async with self.db:
            new_hash, wants_postpone = self._classify_execution(
                run, new_hash, new_inp_hashes, unexpected_input_changes
            )
            self.workflow.update_file_hashes(
                new_out_hashes,
                HashUpdateCause.SUCCEEDED if run.success else HashUpdateCause.FAILED,
            )
            run.detached, run.interrupted_postpone = step.completed(new_hash, wants_postpone)
            self.scheduler.record_stop_time(step.i, succeeded=new_hash is not None)
            if run.detached:
                # The step's creator moved on without it before/when it finished (see
                # Step.detach()): the raw result is moot, report() shows a dedicated
                # explanatory page instead of the raw error/success info.
                run.stderr = ""
            elif wants_postpone and not run.interrupted_postpone:
                # Erase error info to keep the screen output concise.
                run.stderr = ""
            # Persist the captured output in the same transaction as completed(),
            # so a crash cannot leave a completed step without its output (or vice
            # versa). run.stdout/run.stderr stay untruncated; store_output truncates a
            # copy internally, so report() below still forwards the full text to the TUI.
            max_output_size = int(os.getenv("STEPUP_MAX_OUTPUT_SIZE", "0"))
            step.store_output(run.stdout, run.stderr, max_output_size)
        await self._report_step_counts()

        # Report the result of running the step
        await self._report_run(run)

        if unexpected_input_changes:
            # Changes to inputs are suspect and can break everything.
            # End the build phase gracefully by putting the scheduler on hold.
            await self._hold_for_unexpected_input_changes()

    #
    # Job function helper methods
    #

    def _classify_execution(
        self,
        run: Run,
        new_hash: StepHash | None,
        new_inp_hashes: list[tuple[str, FileHash]],
        unexpected_input_changes: bool,
    ) -> tuple[StepHash | None, bool]:
        """Decide success/failure/postpone for a just-executed step, updating `run` in place.

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
        wants_postpone
            Whether the step should be postponed instead of completed.
        """
        wants_postpone = len(run.unavailable) > 0 or len(run.unfresh) > 0

        if unexpected_input_changes:
            # This is the worst case: inputs should never change while a step is running.
            # If they do, fail hard and stop ASAP.
            # Some of the stopping logic is found in `execute_job`, after this classification.
            run.success = False
            new_hash = None
            # Clear the amended inputs to mark the step as failed instead of pending.
            run.unavailable.clear()
            run.unfresh.clear()
            wants_postpone = False
            self.workflow.update_file_hashes(new_inp_hashes, HashUpdateCause.FAILED)
        elif wants_postpone:
            # Rescheduling in the completed() method needs the new hash to be None,
            # so the step is not marked as succeeded.
            run.success = False
            new_hash = None
        elif not run.success:
            # Some other failure occurred (command failed, output missing, etc.)
            new_hash = None

        return new_hash, wants_postpone

    async def _report_step_counts(self) -> None:
        """Send updated step-state counts to the reporter, throttled by elapsed time.

        This skips the underlying `get_step_counts()` scan (and the RPC call) entirely
        when the reporter has no live progress display to feed (`live_progress` is
        `False`), and otherwise when the previous report was sent less than
        `STEP_COUNTS_REPORT_INTERVAL` ago.
        """
        if not self.live_progress:
            return
        now = perf_counter()
        if (
            self._last_step_counts_time is not None
            and now - self._last_step_counts_time < STEP_COUNTS_REPORT_INTERVAL
        ):
            return
        self._last_step_counts_time = now
        async with self.db:
            step_counts = self.workflow.get_step_counts()
        await self.reporter.update_step_counts(step_counts)

    async def _reset_step_to_pending(self, step: Step) -> None:
        """Discard a step's stored hash and transition it back to PENDING for re-execution."""
        async with self.db:
            step.reset_for_rerun()
            step.delete_hash()
            step.set_state(StepState.PENDING)

    async def _new_run(
        self,
        job_i: int,
        step: Step,
        inp_hashes: list[tuple[str, FileHash]],
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
            The input file hashes for the step, as a list of `(path, FileHash)` tuples.
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
        # (see `Executor.interrupt`), in which case `new_inp_hashes` is empty and `run` has
        # already been marked failed by `_run_work_thread`, or the hashes of the input files
        # on disk differ from those in the database, or some inputs were deleted. The latter
        # breaks the workflow, so the step is flagged as failed and the scheduler held.
        unexpected_input_changes = len(new_inp_hashes) > 0
        if unexpected_input_changes:
            async with self.db:
                self.workflow.update_file_hashes(new_inp_hashes, HashUpdateCause.FAILED)
        await self._finalize_failed_run(run)
        if unexpected_input_changes:
            await self._hold_for_unexpected_input_changes()
        return run, None

    async def _finalize_failed_run(self, run: Run) -> None:
        """Complete, record and report a run that failed before producing a new step hash."""
        async with self.db:
            run.step.completed(None, False)
        self.scheduler.record_stop_time(run.step.i, succeeded=False)
        await self._report_step_counts()
        await self._report_run(run)

    async def _hold_for_unexpected_input_changes(self) -> None:
        """Put the scheduler on hold because a step's inputs changed unexpectedly."""
        self.scheduler.on_hold = True
        await self.reporter(
            "ERROR", "The scheduler has been put on hold due to unexpected input changes."
        )

    #
    # Hash computation helpers
    #

    async def _run_work_thread(self, run: Run, work: Callable[[threading.Event], Any]) -> Any:
        """Run a GIL-releasing computation in a thread.

        Returns `None` if the computation was cancelled by an interrupted shutdown
        (see `Executor.interrupt`), in which case `run` has already been marked failed.
        """
        with self._track_running(run):
            worker = ThreadWorker(work=work, job_i=run.job_i)
            run.worker = worker
            try:
                return await worker.run_in_thread()
            except HashCancelledError:
                run.success = False
                run.stderr += (
                    "\n" if run.stderr else ""
                ) + "Hash computation was cancelled because the build is shutting down."
                return None
            finally:
                run.worker = None

    @contextmanager
    def _track_running(self, run):
        """Context manager to track a worker as running."""
        if run.job_i in self.running:
            raise RuntimeError(f"Run {run.job_i} is already tracked as running.")
        self.running[run.job_i] = run
        try:
            yield
        finally:
            del self.running[run.job_i]

    async def _compute_inp_step_hash(
        self,
        run: Run,
        inp_hashes: list[tuple[str, FileHash]],
        env_deps: list[str],
    ) -> tuple[StepHash | None, list[tuple[str, FileHash]]]:
        """Compute the input part of a step hash and apply it to `run`."""
        result = await self._run_work_thread(run, functools.partial(compute_inp_hashes, inp_hashes))
        if result is None:
            return None, []

        # If there are unexpected issues with inputs, bail out.
        if len(result.messages) > 0:
            run.inp_messages.extend(result.messages)
            run.success = False
            return None, result.new_hashes

        # Get some info from the workflow to include in the step hash.
        async with self.db:
            subshell = run.step.get_subshell()
            env_overrides = run.step.get_env_overrides()

        step_hash = StepHash.from_inp(
            run.step.label,
            self.explain_rerun,
            result.all_hashes,
            {name: self.base_env.get(name) for name in env_deps},
            subshell,
            env_overrides or {},
        )
        run.inp_digest = step_hash.inp_digest
        return step_hash, []

    async def _compute_out_step_hash(
        self, run: Run, step_hash: StepHash
    ) -> tuple[StepHash | None, list[tuple[str, FileHash]]]:
        """Compute the output part of a step hash and apply it to `run`.

        Returns
        -------
        step_hash
            `None` if the hash computation was cancelled by an interrupted shutdown.
        new_out_hashes
            The output file hashes that differed from what was expected before the step ran.
        """
        async with self.db:
            out_hashes = [(rec.path, rec.hash) for rec in run.step.out_paths()]

        result = await self._run_work_thread(run, functools.partial(compute_out_hashes, out_hashes))
        if result is None:
            return None, []

        if len(result.messages) > 0:
            run.out_missing.extend(result.messages)
            run.success = False
        step_hash = step_hash.evolve_out(result.all_hashes)

        return step_hash, result.new_hashes

    async def _compute_full_step_hash(
        self, run: Run
    ) -> tuple[StepHash | None, list[tuple[str, FileHash]], list[tuple[str, FileHash]]]:
        """Compute a new step hash with updated input and output file hashes, applied to `run`."""
        async with self.db:
            # Some inputs may be amended and still unavailable,
            # for which checking hashes is too early.
            # Therefore, only check the hashes of built and static files.
            inp_hashes = [
                (rec.path, rec.hash)
                for rec in run.step.inp_paths()
                if rec.state in (FileState.BUILT, FileState.STATIC)
            ]
            env_deps = list(run.step.env_deps())
            out_hashes = [(rec.path, rec.hash) for rec in run.step.out_paths()]
            subshell = run.step.get_subshell()
            env_overrides = run.step.get_env_overrides()

        result = await self._run_work_thread(
            run, functools.partial(compute_both_hashes, inp_hashes, out_hashes)
        )
        if result is None:
            return None, [], []

        inp_result, out_result = result

        if len(inp_result.messages) == 0:
            step_hash = StepHash.from_inp(
                run.step.label,
                self.explain_rerun,
                inp_result.all_hashes,
                {name: self.base_env.get(name) for name in env_deps},
                subshell,
                env_overrides or {},
            )
            step_hash = step_hash.evolve_out(out_result.all_hashes)
        else:
            step_hash = None
            run.inp_messages.extend(inp_result.messages)
            run.success = False
        if len(out_result.messages) > 0:
            run.out_missing.extend(out_result.messages)
            run.success = False

        return step_hash, inp_result.new_hashes, out_result.new_hashes

    #
    # Command execution helper
    #

    async def _run_command(self, run: Run):
        """Run the command of the step described by `run`."""
        await self.reporter("START", run.description)

        command, workdir = run.step.command_workdir
        async with self.db:
            subshell = run.step.get_subshell()
            need = run.step.get_need()
            env_overrides = run.step.get_env_overrides()

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
        # Note: the variables defined here should be listed in stepup.core.api.getenv

        if self.show_perf:
            pt_initial = perf_counter()

        with self._track_running(run):
            outcome = await launch_command(
                command, subshell=subshell, env=env, cwd=workdir, mp_ctx=self.mp_ctx, run=run
            )

        returncode, stdout, stderr = outcome.payload
        usage = outcome.usage
        self.step_accumulator.add_usage(usage)
        run.returncode = returncode
        run.stdout = stdout
        run.stderr = stderr

        if self.show_perf:
            wtime = perf_counter() - pt_initial
            ru_lines = [
                f"User CPU time [s]:   {usage.utime:9.4f}",
                f"System CPU time [s]: {usage.stime:9.4f}",
                f"Total CPU time [s]:  {usage.utime + usage.stime:9.4f}",
                f"Wall time [s]:       {wtime:9.4f}",
            ]
            run.perf_info = "\n".join(ru_lines)
        if run.returncode != 0:
            run.success = False

    #
    # Reporting helpers
    #

    async def _report_run(self, run: Run):
        pages = await self._build_report_pages(run)
        action = self._determine_action(run)
        if action == "FAIL" and not self.keep_going:
            self.scheduler.on_hold = True
        await self.reporter(action, run.description, pages)

    async def _build_report_pages(self, run: Run) -> list[tuple[str, str]]:
        """Build the report pages describing what happened during a step's execution."""
        command, workdir = run.step.command_workdir
        pages = []
        needs_postpone = not (
            (len(run.unavailable) == 0 and len(run.unfresh) == 0) or run.interrupted_postpone
        )
        if run.detached:
            pages.append(
                (
                    "Step detached",
                    "This step's creator did not recreate it before it finished.\n"
                    "Its result has been discarded, and it will be executed again if recreated.",
                )
            )
        elif not (run.success or needs_postpone):
            # Format command for display (can be copied and pasted into a shell); a non-zero
            # return code is appended as a trailing `# exit=N` comment by format_subprocess.
            async with self.db:
                subshell = run.step.get_subshell()
            pages.append(
                (
                    f"Postponed more than {self.workflow.postpone_cap} times"
                    if run.interrupted_postpone
                    else "Failed command",
                    format_subprocess(command, str(workdir), None, run.returncode, shell=subshell),
                )
            )
        if len(run.perf_info) > 0:
            pages.append(("Performance details", run.perf_info))
        if len(run.unavailable) > 0:
            pages.append(("Unavailable amended inputs", "\n".join(sorted(run.unavailable))))
        if len(run.unfresh) > 0:
            pages.append(("Unfresh amended inputs", "\n".join(sorted(run.unfresh))))
        if len(run.inp_messages) > 0:
            run.inp_messages.sort()
            pages.append(("Invalid inputs", "\n".join(run.inp_messages)))
        if not (needs_postpone or run.detached) and len(run.out_missing) > 0:
            # Do not show missing outputs, as they are fairly normal and harmless when
            # postponing, or when the step was detached.
            run.out_missing.sort()
            pages.append(("Expected outputs not created", "\n".join(run.out_missing)))
        stdout = run.stdout.rstrip()
        if len(stdout) > 0:
            pages.append(("Standard output", stdout))
        stderr = run.stderr.rstrip()
        if len(stderr) > 0:
            pages.append(("Standard error", stderr))
        return pages

    def _determine_action(self, run: Run) -> str:
        """Derive the reporter action string (`SUCCESS`, `FAIL`, ...) for a finished step."""
        if run.detached:
            return "DETACHED"
        if run.interrupted_postpone:
            return "FAIL"
        if len(run.unavailable) > 0 or len(run.unfresh) > 0:
            return "POSTPONED"
        if run.success:
            return "SUCCESS"
        return "FAIL"

    async def _skip(self, run: Run, step_hash: StepHash):
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

    async def _outdated_amended(self, run: Run, old_hash: StepHash, new_hash: StepHash):
        if self.explain_rerun:
            page_change, page_same = compare_step_hashes(old_hash, new_hash)
            pages = [("Outdated amended step information", page_change)]
            if len(page_same) > 0:
                pages.append(("Remained the same (or missing)", page_same))
            await self.reporter("DROPAMEND", run.description, pages)
