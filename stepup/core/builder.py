# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""The `Builder` drives the build by pulling runnable jobs and sending them to the executor.

`Builder` always runs a single **build phase** per `run_phase()` call: it waits for the
`resume` event, executes all currently runnable jobs via `job_loop`, and ends with
`finalize`, which reverts optional steps, reports pending/failed steps, and removes
outdated outputs. `Builder` has no notion of watch mode; repeating build phases and
deciding what happens between them (e.g. handing control to a `Watcher`) is the caller's
responsibility, see `build_loop` in `director.py`.

The module also contains the standalone helpers `revert_optional`, `report_completion`,
and `remove_outdated_outputs` that are called during finalization.
"""

import asyncio
import logging
import signal
from collections.abc import Collection

import attrs
from path import Path

from .asyncio import wait_for_events
from .enums import FileState, HashUpdateCause, Need, ReturnCode, StepState
from .executor import Executor
from .hash import FileHash
from .hash_queue import HashJob, HashQueue
from .job import Job
from .reporter import ReporterClient
from .scheduler import Scheduler
from .sqlite3 import DBSession
from .utils import reset_joblog
from .workflow import Workflow

__all__ = ("Builder",)


logger = logging.getLogger(__name__)


@attrs.define
class Builder:
    njob: int = attrs.field(kw_only=True)
    """The maximum number of steps to run concurrently."""

    wake_job_loop: asyncio.Event = attrs.field(init=False, factory=asyncio.Event)
    """Event that is set whenever `job_loop` should re-poll the scheduler.

    This now includes:
    - A running task finished (freeing a slot).
    - A new step was defined, which may already be runnable.
    - Files were confirmed static, meaning depending steps may start.
    """

    scheduler: Scheduler = attrs.field(kw_only=True)
    """The scheduler providing jobs to the builder."""

    workflow: Workflow = attrs.field(kw_only=True)
    """The workflow which generated the jobs and which gets updated as a result of the jobs."""

    db: DBSession = attrs.field(kw_only=True)
    """Lock for workflow database access."""

    reporter: ReporterClient = attrs.field(kw_only=True)
    """A reporter client for sending progress info to."""

    live_progress: bool = attrs.field(kw_only=True)
    """Whether the reporter is an interactive terminal that wants live step-count updates."""

    resume: asyncio.Event = attrs.field(init=False, factory=asyncio.Event)
    """Other parts of StepUp can set the resume event to put the builder back to work."""

    running_tasks: dict[asyncio.Task, Job | HashJob] = attrs.field(init=False, factory=dict)
    """Dictionary of asyncio tasks that are currently running a job or hash job."""

    done_tasks: dict[asyncio.Task, Job | HashJob] = attrs.field(init=False, factory=dict)
    """Dictionary of asyncio tasks that have completed a job or hash job."""

    returncode: ReturnCode = attrs.field(init=False, default=ReturnCode.PENDING)
    """Exit code for the director, based on the last build phase."""

    do_remove_outdated: bool = attrs.field(kw_only=True, default=True)
    """Flag to enable removal of outdated outputs."""

    executor: Executor = attrs.field(kw_only=True)
    """The executor that runs the steps as asyncio tasks in this process."""

    hash_queue: HashQueue = attrs.field(kw_only=True)
    """The hash-job queue, drained with priority over `scheduler.pop_runnable_job()`."""

    @hash_queue.default
    def _default_hash_queue(self) -> HashQueue:
        return HashQueue(wake=self.wake_job_loop)

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

    async def job_loop(self):
        """Run all runnable jobs until there are non left or the scheduler is on hold."""
        if self.live_progress:
            async with self.db:
                nsuccess, ntotal = self.workflow.get_counts()
            await self.reporter.update_counts(nsuccess, ntotal)
        await self.reporter("PHASE", "build")
        if self.executor.do_joblog:
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
                    await self.start_task(job)
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
        async with self.db:
            self.scheduler.build_completed()
        await revert_optional(self.db, self.workflow, self.reporter)
        self.returncode = await report_completion(
            self.db, self.workflow, self.scheduler, self.reporter
        )
        if self.returncode.value != 0:
            await self.reporter("WARNING", "Skipping file cleanup due to incomplete build")
        elif not self.do_remove_outdated:
            await self.reporter("WARNING", "Skipping file cleanup at user's request (--no-clean)")
        else:
            async with self.db:
                self.workflow.clean()
            await remove_outdated_outputs(self.workflow, self.db, self.reporter)
        if self.live_progress:
            async with self.db:
                nsuccess, ntotal = self.workflow.get_counts()
            await self.reporter.update_counts(nsuccess, ntotal)
        await self.reporter.check_logs()

    async def start_task(self, job: Job):
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
        """
        self.reporter.start_job(job.prefix[0], job.step.label, job.job_i)
        try:
            return await job.coro(self.executor)
        finally:
            self.reporter.stop_job(job.job_i)

    def start_hash_task(self, hash_job: HashJob) -> None:
        """Start an asyncio task that runs `hash_job` on the executor.

        Sibling of `start_task`, kept separate rather than unified with it: `HashJob`
        bypasses the `Job` task plumbing entirely (see `_run_hash_task_with_progress`),
        so the two families of tasks share `running_tasks`/`done_tasks` for concurrency
        accounting but not their per-task coroutine.
        """
        task = asyncio.create_task(
            self._run_hash_task_with_progress(hash_job), name=f"HASH: {hash_job.path}"
        )
        self.running_tasks[task] = hash_job
        task.add_done_callback(self._task_done)

    async def _run_hash_task_with_progress(self, hash_job: HashJob):
        """Run `hash_job` on the executor, bracketed by progress-bar start/stop calls.

        Mirrors `_run_with_progress`: hash jobs are user-visible progress items too, and
        the bracket must cover the whole task body (not just the time inside
        `Executor.run_hash_job`) so the job shows as running from the moment its task
        starts. `HashJob.job_i` is negative (see `hash_queue.py`), so it can never collide
        with a real `Step.i` in the reporter/progress-bar dict, which is keyed by whatever
        int it is given.
        """
        self.reporter.start_job("H", hash_job.path, hash_job.job_i)
        try:
            return await self.executor.run_hash_job(hash_job)
        finally:
            self.reporter.stop_job(hash_job.job_i)

    async def run_promoted_hash_jobs(
        self, paths_hashes: Collection[tuple[str, FileHash]], cause: HashUpdateCause
    ) -> None:
        """Submit and run hash jobs immediately, bypassing `job_loop`'s `njob` budget.

        Used by `amend()` when a step blocks on still-`UNCONFIRMED` inputs: the awaiting
        step already holds a slot and is idle while it waits, so running its hash jobs
        outside the budget (instead of queuing them, where `njob` steps all blocked in
        `amend()` could starve them forever) keeps real concurrent work roughly at `njob`.
        Goes through `_run_hash_task_with_progress`, like every other hash job,
        so a promoted job is equally visible in the progress bar.

        Parameters
        ----------
        paths_hashes
            `(path, old_hash)` pairs to (re)hash.
        cause
            Passed through to every submitted job; see `HashJob.cause`.
        """

        async def run_one(path: str, old_hash: FileHash) -> None:
            job = self.hash_queue.submit(path, old_hash, cause)
            if self.hash_queue.claim(job):
                await self._run_hash_task_with_progress(job)
            # Await (rather than just check) the shared future even after running it here:
            # `run_hash_job` swallows per-file errors into the future instead of raising,
            # so this is what lets an exception (e.g. a stat error) propagate to the
            # `amend()` caller instead of being silently lost.
            await asyncio.shield(job.future)

        await asyncio.gather(*(run_one(path, old_hash) for path, old_hash in paths_hashes))

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
            if isinstance(job, HashJob):
                # No Scheduler bookkeeping for hash jobs: they never went through
                # scheduler.pop_runnable_job(), so job.job_i isn't a key in
                # scheduler.jobs, and there is no Step to record a duration for.
                self.wake_job_loop.set()
                continue
            await self.scheduler.job_completed(job)
            self.wake_job_loop.set()

    async def stop(self):
        """Cancel any still-running step tasks and signal their child processes."""
        self.executor.interrupt(signal.SIGTERM)
        # Started hash jobs are covered by executor.interrupt() above, through
        # Executor.running; this covers the queued-but-not-yet-started ones, whose
        # futures would otherwise hang forever.
        self.hash_queue.shutdown()
        tasks = list(self.running_tasks)
        for task in tasks:
            task.cancel()
        if len(tasks) > 0:
            await asyncio.gather(*tasks, return_exceptions=True)
        # Best-effort rescue of durations accumulated during a phase that ended without
        # reaching finalize(), e.g. because a step task raised an unexpected exception.
        # Never let a flush failure mask the original error or block shutdown.
        try:
            async with self.db:
                self.scheduler.build_completed()
        except Exception:  # noqa: BLE001
            logger.warning("Failed to flush step durations during shutdown.", exc_info=True)

    async def interrupt_tasks(self, sig: int):
        self.executor.interrupt(sig)


CREATE_TEMP_TABLE_STEP = f"""
-- Find all optional steps
CREATE TEMP TABLE optional_step AS
SELECT step.node AS i, node.label, step.state
FROM step
JOIN node ON step.node = node.i
WHERE _implied_need = {Need.OPTIONAL.value}
AND NOT node.detached
"""

CREATE_TEMP_TABLE_FILE = f"""
-- Find all files that are outputs or volatile of optional steps.
CREATE TEMP TABLE optional_to_be_deleted AS
SELECT node.i, node.label, file.state, file.hash
FROM file
JOIN node ON file.node = node.i
JOIN dependency ON dependency.sink = node.i
JOIN optional_step ON dependency.source = optional_step.i
WHERE file.state
IN ({FileState.VOLATILE.value}, {FileState.BUILT.value}, {FileState.OUTDATED.value})
"""

UPDATE_OPTIONAL_STEPS = f"""
UPDATE step SET state = {StepState.PENDING.value}
FROM optional_step
WHERE step.node = optional_step.i
AND step.state != {StepState.PENDING.value}
"""

SELECT_OPTIONAL_TO_BE_DELETED = """
SELECT label, state, hash FROM optional_to_be_deleted
"""

UPDATE_OPTIONAL_TO_BE_DELETED = f"""
UPDATE file
SET state = {FileState.AWAITED.value}, hash = NULL
FROM optional_to_be_deleted
WHERE file.node = optional_to_be_deleted.i
"""

DROP_TEMP_TABLE_STEP = """
DROP TABLE IF EXISTS optional_step
"""

DROP_TEMP_TABLE_FILE = """
DROP TABLE IF EXISTS optional_to_be_deleted
"""


async def revert_optional(db: DBSession, workflow: Workflow, reporter: ReporterClient):
    """Revert optional steps that have previously been executed to pending again."""
    async with db:
        db = workflow.db
        # Get the optional steps that are not pending, and mark them pending again.
        db.execute(DROP_TEMP_TABLE_STEP)
        db.execute(DROP_TEMP_TABLE_FILE)
        db.execute(CREATE_TEMP_TABLE_STEP)
        db.execute(CREATE_TEMP_TABLE_FILE)
        cur = db.execute(UPDATE_OPTIONAL_STEPS)
        nstep = cur.rowcount
        cur = db.execute(SELECT_OPTIONAL_TO_BE_DELETED)
        to_be_deleted = [
            (row[0], None if row[1] == FileState.VOLATILE.value else FileHash.from_json(row[2]))
            for row in cur
        ]
        if len(to_be_deleted) > 0:
            # Mark the files for deletion and reset their state in the database.
            workflow.to_be_deleted.extend(to_be_deleted)
            db.execute(UPDATE_OPTIONAL_TO_BE_DELETED)
        db.execute(DROP_TEMP_TABLE_STEP)
        db.execute(DROP_TEMP_TABLE_FILE)
    # Report the reverted steps and the files that are marked for deletion.
    if nstep > 0 or len(to_be_deleted) > 0:
        await reporter(
            "WARNING",
            f"Reverted {nstep} optional step(s) to PENDING and "
            f"marked {len(to_be_deleted)} output/volatile file(s) for deletion.",
        )


async def report_completion(
    db: DBSession, workflow: Workflow, scheduler: Scheduler, reporter: ReporterClient
) -> ReturnCode:
    """Report parts of the workflow that could not be executed."""
    returncode = ReturnCode(0)
    async with db:
        steps_failed = list(workflow.steps(StepState.FAILED))
    nfailed = len(steps_failed)
    if nfailed > 0:
        returncode |= ReturnCode.FAILED
        await reporter("WARNING", f"{nfailed} step(s) failed.")

    if scheduler.on_hold:
        returncode |= ReturnCode.ONHOLD
        await reporter("WARNING", "Scheduler is put on hold. Not reporting pending steps.")
        # The missing-target checks further down are skipped too: the build phase ended
        # early, so steps that would have declared a target as output may not have run yet,
        # making a "not produced" warning unreliable.
        return returncode

    async with db:
        step_records = scheduler.get_pending_step_records()
    npending = len(step_records)
    if npending > 0:
        pending_pages = []
        async with db:
            for step, reason in step_records:
                command, workdir = step.command_workdir

                reason_text = {
                    "runnable": "runnable but not executed (builder was interrupted)",
                    "inputs": "required inputs are unavailable",
                    "resources": "required resources exceed maximum available",
                    "unsafe": "creator is not RUNNING or SUCCEEDED",
                }[reason]
                lines = [
                    f"Reason                {reason_text}",
                    f"Command               {command}",
                ]
                if workdir != ".":
                    lines.append(f"Working directory     {workdir}")

                prefix = "Declares"
                for rec in step.static_paths():
                    lines.append(f"{prefix}      STATIC  {rec.path}")
                    prefix = "        "

                prefix = "Declares"
                for rec in step.missing_paths():
                    lines.append(f"{prefix}       MISSING  {rec.path}")
                    prefix = "        "

                prefix = "Inputs"
                for rec in step.inp_paths(include_detached=True):
                    path_fmt = f"({rec.path})" if rec.detached else rec.path
                    path_fmt = f"{path_fmt} [amended]" if rec.amended else path_fmt
                    lines.append(f"{prefix}      {rec.state.name:>8s}  {path_fmt}")
                    prefix = "      "

                prefix = "Outputs"
                for rec in step.out_paths():
                    path_fmt = f"{rec.path} [amended]" if rec.amended else rec.path
                    lines.append(f"{prefix}     {rec.state.name:>8s}  {path_fmt}")
                    prefix = "       "

                for rec in step.vol_paths():
                    path_fmt = f"{rec.path} [amended]" if rec.amended else rec.path
                    lines.append(f"{prefix}     VOLATILE  {path_fmt}")
                    prefix = "       "

                prefix = "Resource"
                resources = workflow.db.execute(
                    "SELECT name, units FROM step_resource WHERE node = ?",
                    (step.i,),
                )
                for name, units in resources:
                    lines.append(f"{prefix} {name:>11s}  {units}")
                    prefix = "        "

                pending_pages.append(("PENDING Step", "\n".join(lines)))

        if npending > 0:
            returncode |= ReturnCode.PENDING
            descr = f"{npending} step(s) remained pending ..."
            async with db:
                # Insert pages with detached and missing inputs in front.
                detached_page = "\n".join(
                    f"{file_state.name:>20s}  {path}"
                    for path, file_state in workflow.detached_inp_paths()
                )
                missing_page = "\n".join(
                    f"             MISSING  {path}" for path in workflow.missing_paths()
                )
            if detached_page != "":
                pending_pages.insert(0, ("Detached inputs", detached_page))
            if missing_page != "":
                pending_pages.insert(0, ("Missing inputs", missing_page))
            # Finally, report the workflow steps that are pending.
            await reporter("WARNING", descr, pending_pages)

    # Targets that never became the regular output of an active step: reported after the
    # build phase completes, since dynamically-declared steps may only appear once earlier
    # steps run, so this cannot be checked upfront.
    async with db:
        missing_targets = [
            target for target in sorted(workflow.targets) if not workflow.is_regular_output(target)
        ]
    if missing_targets:
        returncode |= ReturnCode.NOTPRODUCED
        await reporter(
            "WARNING",
            f"{len(missing_targets)} target(s) are not produced by any step in the workflow: "
            + ", ".join(missing_targets),
        )

    # Directory targets that matched zero regular outputs: weaker than the exact-target
    # warning above by design (best-effort semantics), see Workflow.dir_has_regular_output.
    async with db:
        missing_target_dirs = [
            target_dir
            for target_dir in sorted(workflow.target_dirs)
            if not workflow.dir_has_regular_output(target_dir)
        ]
    if missing_target_dirs:
        returncode |= ReturnCode.NOTPRODUCED
        await reporter(
            "WARNING",
            f"{len(missing_target_dirs)} directory target(s) matched no regular output "
            "in the workflow: " + ", ".join(missing_target_dirs),
        )
    return returncode


async def remove_outdated_outputs(workflow: Workflow, db: DBSession, reporter: ReporterClient):
    """Remove outdated outputs from the file system and reset their state in the database."""
    await reporter(
        "DIRECTOR",
        f"Trying to remove {len(workflow.to_be_deleted)} outdated output(s)",
    )
    workflow.to_be_deleted.sort(reverse=True)
    # Remove the files from the file system.
    parents = set()
    for path, file_hash in workflow.to_be_deleted:
        path = Path(path)
        if (file_hash is None or file_hash.regen(path) == file_hash) and _remove_file(path):
            await reporter("REMOVE", path)
            parents.add(path.parent)

    # Clean up empty parent directories.
    parents = sorted(parents)
    while len(parents) > 0:
        parent = parents.pop()
        if parent.is_dir() and not any(parent.iterdir()) and _remove_dir(parent):
            await reporter("REMOVE", parent)
            parent = parent.parent
            if parent.name not in ("..", ".", ""):
                parents.append(parent)

    # Reset the state of the deleted files in the database, if they are still present.
    async with db:
        workflow.db.executemany(
            """
            WITH node_tmp AS (SELECT i FROM node WHERE label = ?)
            UPDATE file
            SET state = ?, hash = NULL
            WHERE node IN node_tmp
            """,
            [(path, FileState.AWAITED.value) for path, _ in workflow.to_be_deleted],
        )
    workflow.to_be_deleted.clear()


def _remove_file(path: Path) -> bool:
    try:
        path.remove()
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return False


def _remove_dir(path: Path) -> bool:
    try:
        path.rmdir()
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return False
