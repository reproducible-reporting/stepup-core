# StepUp Core provides the basic framework for the StepUp build tool.
# Copyright 2024-2026 Toon Verstraelen
#
# This file is part of StepUp Core.
#
# StepUp Core is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 3
# of the License, or (at your option) any later version.
#
# StepUp Core is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, see <http://www.gnu.org/licenses/>
#
# --
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
from .watcher import Watcher
from .workflow import Workflow

__all__ = ("Builder",)


logger = logging.getLogger(__name__)


@attrs.define
class Builder:
    njob: int = attrs.field(kw_only=True)
    """The maximum number of steps to run concurrently."""

    need_job: asyncio.Event = attrs.field(init=False, factory=asyncio.Event)
    """Event that is set when there is capacity to take another job."""

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

    show_perf: bool = attrs.field(kw_only=True)
    """Flag to enable performance output after a step executed."""

    explain_rerun: bool = attrs.field(kw_only=True)
    """Flag to enable more details on why steps cannot be skipped."""

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

    mp_ctx: object = attrs.field(kw_only=True, default=None)
    """Multiprocessing forkserver context, or None to use plain subprocesses."""

    infra_env: dict = attrs.field(kw_only=True, factory=dict)
    """Environment variables from the director for step child processes, overriding `os.environ`."""

    max_output_size: int = attrs.field(kw_only=True, default=0)
    """Maximum bytes of stdout/stderr stored per step stream in the DB; 0 = unlimited."""

    executor: Executor = attrs.field(init=False)
    """The executor that runs the steps as asyncio tasks in this process."""

    @executor.default
    def _executor_default(self):
        return Executor(
            self.scheduler,
            self.workflow,
            self.db,
            self.reporter,
            self.show_perf,
            self.explain_rerun,
            mp_ctx=self.mp_ctx,
            infra_env=self.infra_env,
            max_output_size=self.max_output_size,
        )

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
        async with self.db:
            step_counts = self.workflow.get_step_counts()
        await self.reporter.update_step_counts(step_counts)
        await self.reporter("PHASE", "build")

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

            # Let the builder wait until a task slot becomes available.
            await self.need_job.wait()
            self.need_job.clear()

    async def finalize(self):
        """Final steps after the builder has executed a bunch of jobs."""
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
        self.need_job.set()

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
            self.need_job.set()

    async def stop(self):
        """Cancel any still-running step tasks and signal their child processes."""
        self.executor.interrupt(signal.SIGTERM)
        tasks = list(self.running_tasks)
        for task in tasks:
            task.cancel()
        if len(tasks) > 0:
            await asyncio.gather(*tasks, return_exceptions=True)

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
SELECT node.i, node.label, file.state, file.digest, file.mode, file.mtime, file.size, file.inode
FROM file
JOIN node ON file.node = node.i
JOIN dependency ON dependency.consumer = node.i
JOIN optional_step ON dependency.supplier = optional_step.i
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
SELECT label, state, digest, mode, mtime, size, inode FROM optional_to_be_deleted
"""

UPDATE_OPTIONAL_TO_BE_DELETED = f"""
UPDATE file
SET state = {FileState.AWAITED.value}, digest = X'75', mode = 0, mtime = 0, size = 0, inode = 0
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
            (row[0], None if row[1] == FileState.VOLATILE.value else FileHash(*row[2:]))
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
        returncode |= ReturnCode.PENDING
        await reporter("WARNING", "Scheduler is put on hold. Not reporting pending steps.")
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
                for path in step.static_paths():
                    lines.append(f"{prefix}      STATIC  {path}")
                    prefix = "        "

                prefix = "Declares"
                for path in step.missing_paths():
                    lines.append(f"{prefix}       MISSING  {path}")
                    prefix = "        "

                prefix = "Inputs"
                for path, state, detached, amended in step.inp_paths(
                    yield_state=True, yield_detached=True, yield_amended=True
                ):
                    path_fmt = f"({path})" if detached else path
                    path_fmt = f"{path_fmt} [amended]" if amended else path_fmt
                    lines.append(f"{prefix}      {state.name:>8s}  {path_fmt}")
                    prefix = "      "

                prefix = "Outputs"
                for path, state, amended in step.out_paths(yield_state=True, yield_amended=True):
                    path_fmt = f"{path} [amended]" if amended else path
                    lines.append(f"{prefix}     {state.name:>8s}  {path_fmt}")
                    prefix = "       "

                for path, amended in step.vol_paths(yield_amended=True):
                    path_fmt = f"{path} [amended]" if amended else path
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
    return returncode


async def remove_outdated_outputs(workflow: Workflow, db: DBSession, reporter: ReporterClient):
    """Remove outdated outputs from the file system and reset their state in the database."""
    await reporter(
        "DIRECTOR",
        f"Trying to delete {len(workflow.to_be_deleted)} outdated output(s)",
    )
    workflow.to_be_deleted.sort(reverse=True)
    # Remove the files from the file system.
    parents = set()
    for path, file_hash in workflow.to_be_deleted:
        path = Path(path)
        if (file_hash is None or file_hash.regen(path) == file_hash) and _remove_file(path):
            await reporter("CLEAN", path)
            parents.add(path.parent)

    # Clean up empty parent directories.
    parents = sorted(parents)
    while len(parents) > 0:
        parent = parents.pop()
        if parent.is_dir() and not any(parent.iterdir()) and _remove_dir(parent):
            await reporter("CLEAN", parent)
            parent = parent.parent
            if parent.name not in ("..", ".", ""):
                parents.append(parent)

    # Reset the state of the deleted files in the database, if they are still present.
    async with db:
        workflow.db.executemany(
            """
            WITH node_tmp AS (SELECT i FROM node WHERE label = ?)
            UPDATE file
            SET state = ?, digest = ?, mode = 0, mtime = 0, size = 0, inode = 0
            WHERE node IN node_tmp
            """,
            [(path, FileState.AWAITED.value, b"u") for path, _ in workflow.to_be_deleted],
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
