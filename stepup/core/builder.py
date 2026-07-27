# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""The `Builder` drives the build by pulling runnable jobs and sending them to the executor.

Each **build phase** starts when the `resume` event is set,
executes all currently runnable jobs via `job_loop`,
and ends with `finalize`, which reverts optional steps,
reports pending/failed steps, removes outdated outputs,
and notifies the `Watcher` to resume file-system monitoring.

The module also contains the standalone helpers `revert_optional`, `report_completion`,
and `remove_outdated_outputs` that are called during finalization.
"""

import asyncio
import logging
import signal

import attrs
from path import Path

from .asyncio import wait_for_events
from .enums import FileState, Need, ReturnCode, StepState
from .executor import Executor
from .hash import FileHash
from .job import Job
from .reporter import ReporterClient
from .scheduler import Scheduler
from .sqlite3 import DBSession
from .utils import reset_joblog
from .watcher import Watcher
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

    watcher: Watcher | None = attrs.field(kw_only=True)
    """The watcher instance, used to start the watcher when the builder becomes idle."""

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

    running_tasks: dict[asyncio.Task, Job] = attrs.field(init=False, factory=dict)
    """Dictionary of asyncio tasks that are currently running a job."""

    done_tasks: dict[asyncio.Task, Job] = attrs.field(init=False, factory=dict)
    """Dictionary of asyncio tasks that have completed a job."""

    returncode: ReturnCode = attrs.field(init=False, default=ReturnCode.PENDING)
    """Exit code for the director, based on the last build phase."""

    do_remove_outdated: bool = attrs.field(kw_only=True, default=True)
    """Flag to enable removal of outdated outputs."""

    executor: Executor = attrs.field(kw_only=True)
    """The executor that runs the steps as asyncio tasks in this process."""

    async def loop(self, stop_event: asyncio.Event):
        """The main builder loop.

        Parameters
        ----------
        stop_event
            The main builder loop is interrupted by this event.

        Notes
        -----
        One iteration in the main builder loop consists of running a bunch of jobs:
        All runnable jobs are executed unless the user interrupts the builder (drain command).
        """
        # Loop through build phases.
        while True:
            await wait_for_events(self.resume, stop_event, return_when=asyncio.FIRST_COMPLETED)
            if stop_event.is_set():
                return
            await self.job_loop()
            await self.finalize()
            self.resume.clear()
            # If there is no watcher, the builder stops after one iteration.
            if self.watcher is None:
                stop_event.set()

    async def job_loop(self):
        """Run all runnable jobs until there are non left or the scheduler is on hold."""
        if self.live_progress:
            async with self.db:
                step_counts = self.workflow.get_step_counts()
            await self.reporter.update_step_counts(step_counts)
        await self.reporter("PHASE", "build")
        if self.executor.do_joblog:
            reset_joblog(self.njob)

        # Get step jobs and run them as asyncio tasks.
        while True:
            # Handle exceptions of done tasks,
            # and give feedback to the scheduler about completed jobs.
            await self.handle_done_tasks()

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
                step_counts = self.workflow.get_step_counts()
            await self.reporter.update_step_counts(step_counts)
        await self.reporter.check_logs()
        if self.watcher is not None:
            self.watcher.resume.set()

    async def start_task(self, job: Job):
        """Start an asyncio task that runs the job in the executor."""
        logger.info("Run %s", job.name)
        task = asyncio.create_task(job.coro(self.executor), name=job.name)
        self.running_tasks[task] = job
        task.add_done_callback(self._task_done)

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
            await self.scheduler.job_completed(job)
            self.wake_job_loop.set()

    async def stop(self):
        """Cancel any still-running step tasks and signal their child processes."""
        self.executor.interrupt(signal.SIGTERM)
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
