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
"""The `Runner` delegates the execution of jobs to the workers."""

import asyncio
import logging
from functools import partial

import attrs
from path import Path

from .asyncio import wait_for_events
from .dispatcher import Dispatcher
from .enums import FileState, Need, ReturnCode, StepState
from .exceptions import RPCError
from .hash import FileHash
from .job import Job
from .reporter import ReporterClient
from .utils import DBLock, myparent, remove_path
from .watcher import Watcher
from .worker import WorkerClient
from .workflow import Workflow

__all__ = ("Runner",)


logger = logging.getLogger(__name__)


@attrs.define
class Runner:
    nworker: int = attrs.field(kw_only=True)
    """The number of worker processes to launch."""

    need_job: asyncio.Event = attrs.field(init=False, factory=asyncio.Event)
    """Event that is set when there is a worker ready to take another job."""

    watcher: Watcher | None = attrs.field(kw_only=True)
    """The watcher instance, used to start the watcher when the runner becomes idle."""

    dispatcher: Dispatcher = attrs.field(kw_only=True)
    """The dispatcher providing jobs to the runner."""

    workflow: Workflow = attrs.field(kw_only=True)
    """The workflow which generated the jobs and which gets updated as a result of the jobs."""

    dblock: DBLock = attrs.field(kw_only=True)
    """Lock for workflow database access."""

    reporter: ReporterClient = attrs.field(kw_only=True)
    """A reporter client for sending progress info to."""

    director_socket_path: Path = attrs.field(kw_only=True)
    """The path of the director socket, passed on to worker processes."""

    show_perf: bool = attrs.field(kw_only=True)
    """Flag to enable performance output after a worker executed a step."""

    explain_rerun: bool = attrs.field(kw_only=True)
    """Flag to enable more details on why steps cannot be skipped."""

    resume: asyncio.Event = attrs.field(init=False, factory=asyncio.Event)
    """Other parts of StepUp can set the resume event to put the runner back to work."""

    workers: list[WorkerClient] = attrs.field(init=False, factory=list)
    """A list of worker client objects, one for each worker process."""

    active_workers: set[int] = attrs.field(init=False, factory=set)
    """The list of active workers (just integer indexes of the workers list)."""

    idle_workers: set[int] = attrs.field(init=False, factory=set)
    """The list of idle workers (just integer indexes of the workers list)."""

    running_worker_tasks: dict[asyncio.Task, Job] = attrs.field(init=False, factory=dict)
    """Dictionary of asyncio tasks that are currently running a job."""

    done_worker_tasks: dict[asyncio.Task, Job] = attrs.field(init=False, factory=dict)
    """Dictionary of asyncio tasks that have completed a job."""

    returncode: ReturnCode = attrs.field(init=False, default=ReturnCode.PENDING)
    """Exit code for the director, based on the last run phase."""

    do_remove_outdated: bool = attrs.field(kw_only=True, default=True)
    """Flag to enable removal of outdated outputs."""

    mp_ctx: object = attrs.field(kw_only=True, default=None)
    """Multiprocessing forkserver context, or None to use subprocesses."""

    async def loop(self, stop_event: asyncio.Event):
        """The main runner loop.

        Parameters
        ----------
        stop_event
            The main runner loop is interrupted by this event.

        Notes
        -----
        One iteration in the main runner loop consists of running a bunch of jobs:
        All runnable jobs are executed unless the user interrupts the runner (drain command).
        """
        # Start workers in parallel.
        await asyncio.gather(
            *[
                self._launch_worker(idx, f".stepup/worker{idx}.log", stop_event)
                for idx in range(self.nworker)
            ]
        )
        self.idle_workers = set(range(self.nworker))
        # Loop through run phases.
        while True:
            await wait_for_events(self.resume, stop_event, return_when=asyncio.FIRST_COMPLETED)
            if stop_event.is_set():
                return
            await self.job_loop()
            await self.finalize()
            self.resume.clear()
            # If there is no watcher, the runner stops after one iteration.
            if self.watcher is None:
                stop_event.set()

    async def job_loop(self):
        """Run all runnable jobs until there are non left or the dispatcher is on hold."""
        async with self.dblock:
            step_counts = self.workflow.get_step_counts()
        await self.reporter.update_step_counts(step_counts)
        await self.reporter("PHASE", "run")

        # Get step jobs and run them on the workers.
        while True:
            # Handle exceptions of done tasks,
            # and give feedback to the dispatcher about completed jobs.
            await self.handle_done_tasks()

            # Get the next job and send it to workers if there is such a job.
            if len(self.running_worker_tasks) < self.nworker:
                job = await self.dispatcher.pop_runnable_job()
                if job is not None:
                    await self.send_to_worker(job)
                    continue

            # When there is nothing left to do, the runner must stop.
            logger.debug(
                "Runner loop: %d running tasks, %d done tasks",
                len(self.running_worker_tasks),
                len(self.done_worker_tasks),
            )
            if len(self.running_worker_tasks) == 0 and len(self.done_worker_tasks) == 0:
                return

            # Let the runner wait until one of the workers becomes idle.
            await self.need_job.wait()
            self.need_job.clear()

    async def finalize(self):
        """Final steps after the runner has executed a bunch of jobs."""
        await revert_optional(self.dblock, self.workflow, self.reporter)
        self.returncode = await report_completion(
            self.dblock, self.workflow, self.dispatcher, self.reporter
        )
        if self.returncode.value != 0:
            await self.reporter("WARNING", "Skipping file cleanup due to incomplete build")
        elif not self.do_remove_outdated:
            await self.reporter("WARNING", "Skipping file cleanup at user's request (--no-clean)")
        else:
            async with self.dblock:
                self.workflow.clean()
            await remove_outdated_outputs(self.workflow, self.dblock, self.reporter)
        async with self.dblock:
            self.workflow.con.execute("VACUUM")
            step_counts = self.workflow.get_step_counts()
        await self.reporter.update_step_counts(step_counts)
        await self.reporter.check_logs()
        if self.watcher is not None:
            self.watcher.resume.set()

    async def send_to_worker(self, job: Job):
        """Select an idle worker and send the job to it."""
        # Select an idle worker.
        if len(self.idle_workers) > 0:
            worker_idx = self.idle_workers.pop()
        else:
            raise RuntimeError("No idle workers available.")
        # Move it to the set of active workers.
        worker = self.workers[worker_idx]
        self.active_workers.add(worker_idx)
        # Send the job to the worker.
        logger.info("Run %s", job.name)
        task = asyncio.create_task(job.coro(worker), name=job.name)
        self.running_worker_tasks[task] = job
        task.add_done_callback(partial(self._task_done, worker_idx=worker_idx))

    async def _launch_worker(self, idx: int, log_path: str, stop_event: asyncio.Event):
        worker = WorkerClient(
            self.dispatcher,
            self.workflow,
            self.dblock,
            self.reporter,
            self.director_socket_path,
            self.show_perf,
            self.explain_rerun,
            idx,
            mp_ctx=self.mp_ctx,
        )
        self.workers.append(worker)
        await worker.boot(log_path, stop_event)
        await self.reporter("DIRECTOR", f"Launched worker {worker.idx}")

    def _task_done(self, task: asyncio.Task, worker_idx: int):
        job = self.running_worker_tasks.pop(task)
        self.done_worker_tasks[task] = job
        if worker_idx in self.active_workers:
            self.active_workers.discard(worker_idx)
            self.idle_workers.add(worker_idx)
        else:
            raise RuntimeError(f"Worker {worker_idx} not in active workers.")
        self.need_job.set()

    async def handle_done_tasks(self):
        """Check whether done tasks raised exceptions and propagate them when found."""
        while len(self.done_worker_tasks) > 0:
            task, job = self.done_worker_tasks.popitem()
            exc = task.exception()
            if exc is not None:
                self.dispatcher.on_hold = True

                msg = f"Exception in worker task {task.get_name()}"
                raise RuntimeError(msg) from exc
            await self.dispatcher.job_completed(job)
            self.need_job.set()

    async def stop_workers(self):
        async def stop_worker(w):
            try:
                await w.shutdown()
            except (ConnectionError, RPCError) as exc:
                # Ignore connection/RPC errors when requesting worker shutdown
                logger.warning(
                    "Ignoring exception when requesting worker shutdown on worker %r: %r",
                    w.idx,
                    exc,
                )
            finally:
                await w.close()

        tasks = []
        while len(self.idle_workers) > 0:
            worker_idx = self.idle_workers.pop()
            tasks.append(stop_worker(self.workers[worker_idx]))
        while len(self.active_workers) > 0:
            worker_idx = self.active_workers.pop()
            tasks.append(stop_worker(self.workers[worker_idx]))
        await asyncio.gather(*tasks)

    async def interrupt_workers(self, signal: int):
        await asyncio.gather(
            *[self.workers[worker_idx].interrupt(signal) for worker_idx in self.active_workers]
        )


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


async def revert_optional(dblock: DBLock, workflow: Workflow, reporter: ReporterClient):
    """Revert optional steps that have previously been executed to pending again."""
    async with dblock:
        con = workflow.con
        # Get the optional steps that are not pending, and mark them pending again.
        con.execute(DROP_TEMP_TABLE_STEP)
        con.execute(DROP_TEMP_TABLE_FILE)
        con.execute(CREATE_TEMP_TABLE_STEP)
        con.execute(CREATE_TEMP_TABLE_FILE)
        cur = con.execute(UPDATE_OPTIONAL_STEPS)
        nstep = cur.rowcount
        cur = con.execute(SELECT_OPTIONAL_TO_BE_DELETED)
        to_be_deleted = [
            (row[0], None if row[1] == FileState.VOLATILE.value else FileHash(*row[2:]))
            for row in cur
        ]
        if len(to_be_deleted) > 0:
            # Mark the files for deletion and reset their state in the database.
            workflow.to_be_deleted.extend(to_be_deleted)
            con.execute(UPDATE_OPTIONAL_TO_BE_DELETED)
        con.execute(DROP_TEMP_TABLE_STEP)
        con.execute(DROP_TEMP_TABLE_FILE)
    # Report the reverted steps and the files that are marked for deletion.
    if nstep > 0 or len(to_be_deleted) > 0:
        await reporter(
            "WARNING",
            f"Reverted {nstep} optional step(s) to PENDING and "
            f"marked {len(to_be_deleted)} output/volatile file(s) for deletion.",
        )


async def report_completion(
    dblock: DBLock, workflow: Workflow, dispatcher: Dispatcher, reporter: ReporterClient
) -> ReturnCode:
    """Report parts of the workflow that could not be executed."""
    returncode = ReturnCode(0)
    async with dblock:
        steps_failed = list(workflow.steps(StepState.FAILED))
    nfailed = len(steps_failed)
    if nfailed > 0:
        returncode |= ReturnCode.FAILED
        await reporter("WARNING", f"{nfailed} step(s) failed.")

    if dispatcher.on_hold:
        returncode |= ReturnCode.PENDING
        await reporter("WARNING", "Dispatcher is put on hold. Not reporting pending steps.")
        return returncode

    async with dblock:
        step_records = dispatcher.get_pending_step_records()
    npending = len(step_records)
    if npending > 0:
        pending_pages = []
        async with dblock:
            for step, reason in step_records:
                action, workdir = step.get_action_workdir()

                reason_text = {
                    "runnable": "runnable but not executed (runner was interrupted)",
                    "inputs": "required inputs are unavailable",
                    "resources": "required resources exceed maximum available",
                    "unsafe": "creator is not RUNNING or SUCCEEDED",
                }[reason]
                lines = [
                    f"Reason                {reason_text}",
                    f"Action                {action}",
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
                resources = workflow.con.execute(
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
            async with dblock:
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


async def remove_outdated_outputs(workflow: Workflow, dblock: DBLock, reporter: ReporterClient):
    """Remove outdated outputs from the file system and reset their state in the database."""
    await reporter(
        "DIRECTOR",
        f"Trying to delete {len(workflow.to_be_deleted)} outdated output(s)",
    )
    workflow.to_be_deleted.sort(reverse=True)
    # Remove the files from the file system.
    parents = set()
    for path, file_hash in workflow.to_be_deleted:
        if (
            path.endswith("/") or file_hash is None or file_hash.regen(path) == file_hash
        ) and remove_path(path):
            await reporter("CLEAN", path)
            parents.add(myparent(path))

    # Clean up empty parent directories.
    parents = sorted(parents)
    while len(parents) > 0:
        parent = parents.pop()
        if parent.is_dir() and not any(parent.iterdir()) and remove_path(parent):
            await reporter("CLEAN", parent)
            parent = parent.parent
            if parent.name not in ("..", ".", ""):
                parents.append(parent)

    # Reset the state of the deleted files in the database, if they are still present.
    async with dblock:
        workflow.con.executemany(
            """
            WITH node_tmp AS (SELECT i FROM node WHERE label = ?)
            UPDATE file
            SET state = ?, digest = ?, mode = 0, mtime = 0, size = 0, inode = 0
            WHERE node IN node_tmp
            """,
            [(path, FileState.AWAITED.value, b"u") for path, _ in workflow.to_be_deleted],
        )
    workflow.to_be_deleted.clear()
