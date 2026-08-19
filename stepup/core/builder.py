# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""The `Builder` drives the build by pulling runnable jobs and sending them to the executor.

`Builder` always runs a single **build phase** per `run_phase()` call: it waits for the
`resume` event, executes all currently runnable jobs via `job_loop`, and ends with
`finalize`, which reports pending/failed steps and, unless cleanup is skipped,
reverts optional steps and removes outdated outputs.
`Builder` has no notion of watch mode; repeating build phases and
deciding what happens between them (e.g. handing control to a `Watcher`) is the caller's
responsibility, see `build_loop` in `director.py`.

The work done by `finalize` is implemented in `finalize.py`, not here.
"""

import asyncio
import logging
import signal
from collections.abc import Mapping

import attrs

from .asyncio import wait_for_events
from .enums import HashUpdateCause, ReturnCode
from .executor import Executor
from .finalize import remove_outdated_outputs, report_completion, revert_optional
from .hash import FileHash
from .hash_queue import HashJob, HashQueue
from .job import Job
from .reporter import ReporterClient
from .scheduler import Scheduler
from .sqlite3 import DBSession
from .utils import reset_joblog
from .workflow import Workflow

__all__ = ("AnyJob", "Builder")


logger = logging.getLogger(__name__)


AnyJob = Job | HashJob
"""Any unit of work the builder tracks as an asyncio task.

Only `job_i` is common to both; everything beyond that is handled by the branches in
`start_task`/`start_hash_task` and `handle_done_tasks`.
"""


@attrs.define
class Builder:
    # References to other StepUp components.

    scheduler: Scheduler = attrs.field(kw_only=True)
    """The scheduler providing jobs to the builder."""

    workflow: Workflow = attrs.field(kw_only=True)
    """The workflow which generated the jobs and which gets updated as a result of the jobs."""

    db: DBSession = attrs.field(kw_only=True)
    """The workflow database session, i.e. the same object as `workflow.db`.

    It is a separate field because the builder uses it directly as an async context manager,
    which acquires exclusive access to the database for the duration of a transaction.
    """

    reporter: ReporterClient = attrs.field(kw_only=True)
    """A reporter client for sending progress info to."""

    executor: Executor = attrs.field(kw_only=True)
    """The executor that runs the steps as asyncio tasks in this process."""

    # Configuration

    njob: int = attrs.field(kw_only=True)
    """The maximum number of steps to run concurrently."""

    live_progress: bool = attrs.field(kw_only=True)
    """Whether the reporter is an interactive terminal that wants live step-count updates."""

    do_remove_outdated: bool = attrs.field(kw_only=True, default=True)
    """Flag to enable removal of outdated outputs."""

    # Internal state

    wake_job_loop: asyncio.Event = attrs.field(init=False, factory=asyncio.Event)
    """Event that is set whenever `job_loop` should re-poll the scheduler.

    Triggers include:
    - A running task finished (freeing a slot).
    - A new step was defined, which may already be runnable.
    - Files were confirmed static, meaning depending steps may start.
    """

    hash_queue: HashQueue = attrs.field(kw_only=True)
    """The hash-job queue, drained with priority over `scheduler.pop_runnable_job()`.

    The builder creates the queue itself, so this field must stay after `wake_job_loop`:
    attrs evaluates defaults in field order and the default below reads that event.
    """

    @hash_queue.default
    def _default_hash_queue(self) -> HashQueue:
        return HashQueue(wake=self.wake_job_loop)

    resume: asyncio.Event = attrs.field(init=False, factory=asyncio.Event)
    """Other parts of StepUp can set the resume event to put the builder back to work."""

    running_tasks: dict[asyncio.Task, AnyJob] = attrs.field(init=False, factory=dict)
    """Dictionary of asyncio tasks that are currently running a job or hash job."""

    done_tasks: dict[asyncio.Task, AnyJob] = attrs.field(init=False, factory=dict)
    """Dictionary of asyncio tasks that have completed a job or hash job."""

    returncode: ReturnCode = attrs.field(init=False, default=ReturnCode.PENDING)
    """Exit code for the director, based on the last build phase."""

    async def run_phase(self, stop_event: asyncio.Event) -> bool:
        """Wait for `resume`, then run a single build phase (`job_loop` + `finalize`).

        Parameters
        ----------
        stop_event
            If set before `resume`, no phase is run.

        Returns
        -------
        ran
            `False` if `stop_event` fired before `resume` was set, meaning no phase ran.
            `True` otherwise, meaning a phase was run and the caller may call `run_phase`
            again to run another one.
        """
        await wait_for_events(self.resume, stop_event, return_when=asyncio.FIRST_COMPLETED)
        if stop_event.is_set():
            return False
        self.resume.clear()
        await self.job_loop()
        await self.finalize()
        return True

    async def _report_counts(self):
        """Send the number of succeeded and total steps to the reporter, if it wants them."""
        if not self.live_progress:
            return
        async with self.db:
            nsuccess, ntotal = self.workflow.count_required_steps()
        await self.reporter.update_counts(nsuccess, ntotal)

    async def job_loop(self):
        """Run all runnable jobs until there are none left or the scheduler is on hold."""
        await self._report_counts()
        await self.reporter("PHASE", "build")
        if self.executor.write_joblog:
            reset_joblog(self.njob)

        # Get step jobs and run them as asyncio tasks.
        while True:
            # Handle exceptions of done tasks,
            # and give feedback to the scheduler about completed jobs.
            await self.handle_done_tasks()

            # Hash jobs jump the queue: their runnability never depends on the workflow
            # database, so there is no reason to make them wait behind a SQL poll. This
            # must not be skipped while scheduler.on_hold is set ("start no new steps"):
            # pending hash jobs are bookkeeping for work already under way and must finish
            # for the phase to end cleanly, which falls out naturally here since on_hold is
            # only enforced inside scheduler.pop_runnable_job().
            if len(self.running_tasks) < self.njob:
                hash_job = self.hash_queue.pop_nowait()
                if hash_job is not None:
                    self.start_hash_task(hash_job)
                    continue

            # Get the next job and start it as a task if there is such a job.
            if len(self.running_tasks) < self.njob:
                job = await self.scheduler.pop_runnable_job()
                if job is not None:
                    self.start_task(job)
                    continue

            # When there is nothing left to do, the builder must stop.
            logger.debug(
                "Builder loop: %d running tasks, %d done tasks",
                len(self.running_tasks),
                len(self.done_tasks),
            )
            if len(self.running_tasks) == 0 and len(self.done_tasks) == 0:
                return

            # Let the builder wait until there is something new to check.
            await self.wake_job_loop.wait()
            self.wake_job_loop.clear()

    async def finalize(self):
        """Final steps after the builder has executed a bunch of jobs."""
        await self.reporter("DIRECTOR", f"Ran {self.scheduler.job_counter} job(s).")
        self.returncode = await report_completion(
            self.db, self.workflow, self.scheduler, self.reporter
        )
        # Reverting optional steps resets their outputs in the database, which only makes sense
        # paired with removing the files from disk, so it shares the guard below with the rest
        # of the cleanup.
        #
        # A build restricted to targets leaves the outputs of every step outside the target's
        # dependencies OUTDATED, so cleaning up would delete results the user never asked to
        # rebuild. `ReturnCode.WARNING` is masked out below because a warning on its own does
        # not make the build incomplete.
        if len(self.workflow.targets) > 0 or len(self.workflow.target_dirs) > 0:
            await self.reporter(
                "WARNING", "Skipping file cleanup for a build restricted to targets"
            )
        elif self.returncode & ~ReturnCode.WARNING:
            await self.reporter("WARNING", "Skipping file cleanup due to incomplete build")
        elif not self.do_remove_outdated:
            await self.reporter("WARNING", "Skipping file cleanup at user's request (--no-clean)")
        else:
            await revert_optional(self.db, self.workflow, self.reporter)
            async with self.db:
                self.workflow.delete_detached()
            await remove_outdated_outputs(self.db, self.workflow, self.reporter)
        # Step durations and tail times are derived from the settled graph,
        # so this runs after delete_detached().
        await self.scheduler.build_completed()
        await self._report_counts()
        await self.reporter.check_logs()

    def start_task(self, job: Job):
        """Start an asyncio task that runs the job in the executor."""
        logger.info("Run %s", job.name)
        task = asyncio.create_task(self._run_with_progress(job), name=job.name)
        self.running_tasks[task] = job
        task.add_done_callback(self._task_done)

    async def _run_with_progress(self, job: Job):
        """Run `job` on the executor, bracketed by progress-bar start/stop calls.

        The bracket lives here, around the whole job coroutine, rather than at the
        individual start/stop points inside `Executor`: that guarantees a `stop_job` for
        every `start_job`, regardless of which internal path the job takes (skip, rerun,
        early failure, ...), and it shows the job as running from the moment its task
        begins, including input hash computation, not just once the command itself starts.

        Hash jobs get the same treatment from `Executor.run_hash_job` itself,
        since they are also started outside this class (see `gather_hashes`).
        """
        self.reporter.start_job(job.letter, job.label, job.job_i)
        try:
            return await job.coro(self.executor)
        finally:
            self.reporter.stop_job(job.job_i)

    def start_hash_task(self, hash_job: HashJob) -> None:
        """Start an asyncio task that runs `hash_job` on the executor.

        Sibling of `start_task`, sharing the `running_tasks`/`done_tasks` bookkeeping with
        it. Only the parts that genuinely differ stay separate: a hash job is named after
        its path instead of after a step, and it is not logged as a step being run.
        """
        task = asyncio.create_task(
            self.executor.run_hash_job(hash_job), name=f"HASH: {hash_job.path}"
        )
        self.running_tasks[task] = hash_job
        task.add_done_callback(self._task_done)

    async def run_promoted_hash_jobs(
        self, paths_hashes: Mapping[str, FileHash], cause: HashUpdateCause
    ) -> None:
        """Submit and run hash jobs immediately, bypassing `job_loop`'s `njob` budget.

        Used by `amend()` when a step blocks on still-`UNCONFIRMED` inputs: the awaiting
        step already holds a slot and is idle while it waits, so running its hash jobs
        outside the budget (instead of queuing them, where `njob` steps all blocked in
        `amend()` could starve them forever) keeps real concurrent work roughly at `njob`.

        Parameters
        ----------
        paths_hashes
            The old hashes of the files to (re)hash, keyed by path.
        cause
            Passed through to every submitted job; see `HashJob.cause`.
        """

        async def run_one(path: str, old_hash: FileHash) -> None:
            job = self.hash_queue.submit(path, old_hash, cause)
            if self.hash_queue.claim(job):
                await self.executor.run_hash_job(job)
            # Await (rather than just check) the shared future even after running it here:
            # `run_hash_job` swallows per-file errors into the future instead of raising,
            # so this is what lets an exception (e.g. a stat error) propagate to the
            # `amend()` caller instead of being silently lost.
            await asyncio.shield(job.future)

        await asyncio.gather(*(run_one(path, old_hash) for path, old_hash in paths_hashes.items()))

    def _task_done(self, task: asyncio.Task):
        job = self.running_tasks.pop(task)
        self.done_tasks[task] = job
        self.wake_job_loop.set()

    async def handle_done_tasks(self):
        """Check whether done tasks raised exceptions and propagate them when found."""
        while len(self.done_tasks) > 0:
            task, job = self.done_tasks.popitem()
            exc = task.exception()
            if exc is not None:
                self.scheduler.on_hold = True

                msg = f"Exception in task {task.get_name()}"
                raise RuntimeError(msg) from exc
            # Hash jobs get no Scheduler bookkeeping: they never went through
            # scheduler.pop_runnable_job(), so job.job_i isn't a key in
            # scheduler.jobs, and there is no Step to record a duration for.
            if not isinstance(job, HashJob):
                await self.scheduler.job_completed(job)
            self.wake_job_loop.set()

    async def stop(self):
        """Cancel any still-running step tasks and signal their child processes."""
        self.executor.interrupt(signal.SIGTERM)
        # Started hash jobs are covered by executor.interrupt() above, through Executor.running.
        # This covers the queued-but-not-yet-started jobs,
        # whose futures would otherwise hang forever.
        self.hash_queue.shutdown()
        tasks = list(self.running_tasks)
        for task in tasks:
            task.cancel()
        if len(tasks) > 0:
            await asyncio.gather(*tasks, return_exceptions=True)
        # Best-effort rescue of durations accumulated during and refresh derived tail times
        # if the build phase ended without reaching the `finalize()` method,
        # e.g. because a step task raised an unexpected exception.
        # Never let a flush failure mask the original error or block shutdown.
        try:
            await self.scheduler.build_completed()
        except Exception:  # noqa: BLE001
            logger.warning("Failed to flush step durations during shutdown.", exc_info=True)
