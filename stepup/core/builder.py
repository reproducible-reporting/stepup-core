# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""The `Builder` drives the build by pulling runnable jobs and sending them to the executor.

`Builder` always runs a single **build phase** per `run_once()` call:
it waits for the `resume` event,
executes all currently runnable jobs via `job_loop`,
and ends with `finalize`,
which reports pending and failed steps and,
unless cleanup is skipped,
reverts optional steps and removes outdated outputs.
`Builder` has no notion of watch mode:
repeating build phases and deciding what happens between them
(e.g. handing control to a `Watcher`) is the caller's responsibility.
See `build_loop` in `director.py`.

The work done by `finalize` is implemented in `finalize.py`, not here.
"""

import asyncio
import logging
import signal
from collections.abc import Iterable, Mapping

import attrs

from .asyncio import wait_for_any_event
from .enums import HashUpdateCause, ReturnCode
from .executor import Executor
from .finalize import remove_deletable_files, report_unbuilt, revert_optional_steps
from .hash import FileHash
from .hash_queue import HashJob, HashQueue
from .job import Job, init_joblog
from .reporter import ReporterClient
from .scheduler import Scheduler
from .sqlite3 import DBSession
from .workflow import Workflow

__all__ = ("AnyJob", "Builder")


logger = logging.getLogger(__name__)


AnyJob = Job | HashJob
"""Any unit of work the builder tracks as an asyncio task.

Only `job_i` is common to both.
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

    Check the call sites for a full list.
    The builder's `job_loop` waits on this event and does not poll the scheduler on its own,
    meaning that any external cause that may make a job runnable must set this wake event.
    """

    # The builder creates the hash_queue itself, so this field must stay after `wake_job_loop`:
    # attrs evaluates defaults in field order and the default below reads that event.

    hash_queue: HashQueue = attrs.field(init=False)
    """The hash-job queue, drained with priority over `scheduler.pop_next_job()`."""

    @hash_queue.default
    def _default_hash_queue(self) -> HashQueue:
        return HashQueue(wake=self.wake_job_loop)

    resume: asyncio.Event = attrs.field(init=False, factory=asyncio.Event)
    """Other parts of StepUp can set the resume event to put the builder back to work."""

    running_tasks: dict[asyncio.Task, AnyJob] = attrs.field(init=False, factory=dict)
    """Dictionary of asyncio tasks that are currently running a job or hash job."""

    done_tasks: dict[asyncio.Task, AnyJob] = attrs.field(init=False, factory=dict)
    """Dictionary of asyncio tasks that have completed a job or hash job.

    Ordered by completion, and drained in that same order by `handle_done_tasks`,
    so two hash jobs for the same path are applied oldest first.
    """

    returncode: ReturnCode = attrs.field(init=False, default=ReturnCode.PENDING)
    """Exit code for the director, based on the last build phase."""

    async def run_once(self, stop_event: asyncio.Event) -> bool:
        """Wait for `resume`, then run a single build phase (`job_loop` + `finalize`).

        Parameters
        ----------
        stop_event
            If set before `resume`, no phase is run.

        Returns
        -------
        ran
            `False` if `stop_event` fired before `resume` was set, meaning no phase ran.
            `True` otherwise, meaning a phase was run and the caller may call `run_once`
            again to run another one.
        """
        await wait_for_any_event(self.resume, stop_event)
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
        await self.reporter.update_progress(nsuccess, ntotal)

    async def job_loop(self):
        """Run all runnable jobs until there are none left or the scheduler is draining."""
        await self._report_counts()
        await self.reporter("PHASE", "build")
        if self.executor.write_joblog:
            init_joblog(self.njob)

        # Drain runnable work (done tasks, hash jobs, step jobs) as asyncio tasks.
        while True:
            # Handle exceptions of done tasks,
            # and give feedback to the scheduler about completed jobs.
            await self.handle_done_tasks()

            # Hash jobs jump the queue:
            # their runnability never depends on the workflow database,
            # so there is no reason to make them wait behind a SQL poll.
            # This must not be skipped while scheduler.draining is set ("start no new steps"):
            # pending hash jobs are bookkeeping for work already under way
            # and must finish for the phase to end cleanly,
            # which falls out naturally here
            # since draining is only enforced inside scheduler.pop_next_job().
            if len(self.running_tasks) < self.njob:
                hash_job = self.hash_queue.pop_nowait()
                if hash_job is not None:
                    self.start_hash_task(hash_job)
                    continue

            # Get the next job and start it as a task if there is such a job.
            if len(self.running_tasks) < self.njob:
                job = await self.scheduler.pop_next_job()
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
        """Wrap up the build phase after the builder has executed its jobs."""
        await self.reporter("DIRECTOR", f"Ran {self.scheduler.run_counter} job(s).")
        self.returncode = await report_unbuilt(self.workflow, self.scheduler, self.reporter)
        # Reverting optional steps resets their outputs in the database,
        # which only makes sense paired with removing the files from disk,
        # so it shares the guard below with the rest of the cleanup.
        #
        # A build restricted to targets leaves the outputs of every step
        # outside the targets' dependencies OUTDATED,
        # so cleaning up would delete results the user never asked to rebuild.
        # `ReturnCode.WARNING` is masked out below
        # because a warning on its own does not make the build incomplete.
        if len(self.workflow.targets) > 0 or len(self.workflow.target_dirs) > 0:
            await self.reporter(
                "WARNING", "Skipping file cleanup for a build restricted to targets"
            )
        elif self.returncode & ~ReturnCode.WARNING:
            await self.reporter("WARNING", "Skipping file cleanup due to incomplete build")
        elif not self.do_remove_outdated:
            await self.reporter("WARNING", "Skipping file cleanup at user's request (--no-clean)")
        else:
            to_be_deleted = await revert_optional_steps(self.workflow, self.reporter)
            async with self.db:
                to_be_deleted |= self.workflow.delete_detached()
            await remove_deletable_files(to_be_deleted, self.reporter)
        # Step durations and tail times are derived from the settled graph,
        # so this runs after delete_detached().
        await self.scheduler.build_completed()
        await self._report_counts()
        await self.reporter.warn_about_logs()

    def start_task(self, job: Job):
        """Start an asyncio task that runs the job in the executor."""
        logger.info("Run %s", job.name)
        task = asyncio.create_task(self._run_with_progress(job), name=job.name)
        self.running_tasks[task] = job
        task.add_done_callback(self._task_done)

    async def _run_with_progress(self, job: Job):
        """Run `job` on the executor, bracketed by progress-bar start/stop calls.

        The bracket lives here, around the whole job coroutine,
        rather than at the individual start/stop points inside `Executor`:
        that guarantees a `job_stopped` for every `job_started`,
        regardless of which internal path the job takes (skip, rerun, early failure, ...),
        and it shows the job as running from the moment its task begins,
        including input hash computation, not just once the command itself starts.

        Hash jobs get the same treatment from `Executor.run_hash_job` itself,
        since they are also started outside this class (see `gather_hashes`).
        """
        self.reporter.job_started(job.job_i, job.letter, job.label)
        try:
            return await job.coro(self.executor)
        finally:
            self.reporter.job_stopped(job.job_i)

    def start_hash_task(self, hash_job: HashJob) -> None:
        """Start an asyncio task that runs `hash_job` on the executor.

        Sibling of `start_task`, sharing the `running_tasks`/`done_tasks` bookkeeping with it.
        It differs from `start_task` in only two respects:

        1. A hash job is named after its path instead of after a step
        2. It is not logged as a step being run.
        """
        task = asyncio.create_task(
            self.executor.run_hash_job(hash_job), name=f"HASH: {hash_job.path}"
        )
        self.running_tasks[task] = hash_job
        task.add_done_callback(self._task_done)

    async def run_promoted_hash_jobs(self, paths_hashes: Mapping[str, FileHash]) -> None:
        """Submit and run hash jobs immediately, bypassing `job_loop`'s `njob` budget.

        Promoted when a step blocks on still-`UNCONFIRMED` inputs:
        the awaiting step already holds a slot and is idle while it waits,
        so running its hash jobs outside the budget
        (instead of queuing them, where `njob` steps all blocked in `amend()`
        could starve them forever)
        keeps real concurrent work roughly at `njob`.

        Parameters
        ----------
        paths_hashes
            The old hashes of the files to (re)hash, keyed by path.
        """

        async def run_one(job: HashJob) -> FileHash:
            if self.hash_queue.claim(job):
                await self.executor.run_hash_job(job)
            # Await (rather than just check) the shared future even after running it here:
            # `run_hash_job` swallows per-file errors into the future instead of raising,
            # so this is what lets an exception (e.g. a stat error) propagate
            # to the `amend()` caller instead of being silently lost.
            return await asyncio.shield(job.future)

        jobs = [self.hash_queue.submit(path, old_hash) for path, old_hash in paths_hashes.items()]

        # Errors are collected rather than raised straight away,
        # so one unhashable file does not discard the results of the files next to it,
        # which nothing else in this phase would apply.
        results = await asyncio.gather(*(run_one(job) for job in jobs), return_exceptions=True)

        # Apply before returning, so `amend_step`'s re-read of the file states sees the results,
        # whether they were written here or by the other awaiter that won the claim.
        await self._apply_hash_results(jobs)

        for result in results:
            if isinstance(result, BaseException):
                raise result

    async def _apply_hash_results(self, hash_jobs: Iterable[HashJob]) -> None:
        """Write the results of completed hash jobs to the workflow, in one transaction.

        Every job whose result this call claims is applied together,
        which keeps a batch of observations from taking the write lock once per file.

        Parameters
        ----------
        hash_jobs
            Jobs to apply, in completion order.
            A job with nothing to apply is skipped, which covers three cases:
            a future that never resolved because its awaiter was cancelled,
            one that was cancelled or failed
            (`Executor.run_hash_job` has already reported the error and drained the scheduler),
            and one whose result another awaiter already claimed,
            see `HashQueue.claim_apply`.
        """
        results = {}
        for job in hash_jobs:
            future = job.future
            if not future.done() or future.cancelled() or future.exception() is not None:
                continue
            if self.hash_queue.claim_apply(job):
                results[job.path] = future.result()
        if len(results) == 0:
            return
        async with self.db:
            self.workflow.update_file_hashes(results, cause=HashUpdateCause.OBSERVED)

    def _task_done(self, task: asyncio.Task):
        job = self.running_tasks.pop(task)
        self.done_tasks[task] = job
        self.wake_job_loop.set()

    async def handle_done_tasks(self):
        """Retire done tasks: propagate their exceptions, apply hash results, report completions.

        The hash results of all jobs retired by one call are applied in a single transaction.
        This runs before `job_loop` calls `pop_next_job()`,
        so a step waiting on one of these files is not delayed by a single iteration.
        """
        hash_jobs: list[HashJob] = []
        while len(self.done_tasks) > 0:
            # Drained oldest first, so `_apply_hash_results` sees the jobs in completion order
            # and two jobs for the same path leave the later observation in place.
            task = next(iter(self.done_tasks))
            job = self.done_tasks.pop(task)
            exc = task.exception()
            if exc is not None:
                self.scheduler.draining = True

                # Raised before applying anything: the director is tearing down.
                msg = f"Exception in task {task.get_name()}"
                raise RuntimeError(msg) from exc
            # Hash jobs get no Scheduler bookkeeping:
            # they never went through scheduler.pop_next_job(),
            # so job.job_i isn't a key in scheduler.jobs,
            # and there is no Step to record a duration for.
            if isinstance(job, HashJob):
                hash_jobs.append(job)
            else:
                self.scheduler.record_job_completed(job)
            self.wake_job_loop.set()

        await self._apply_hash_results(hash_jobs)

    async def stop(self):
        """Cancel any still-running tasks, signal their child processes and flush step durations."""
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
        # Best-effort rescue of the durations accumulated during the phase,
        # and refresh of the derived tail times,
        # if the build phase ended without reaching `finalize()`,
        # e.g. because a step task raised an unexpected exception.
        # Never let a flush failure mask the original error or block shutdown.
        try:
            await self.scheduler.build_completed()
        except Exception:
            logger.warning("Failed to flush step durations during shutdown.", exc_info=True)
