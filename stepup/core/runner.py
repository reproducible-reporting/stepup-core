# StepUp Core provides the basic framework for the StepUp build tool.
# © 2024–2025 Toon Verstraelen
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
import contextlib
import logging
from functools import partial
from io import TextIOBase

import attrs
from path import Path

from .asyncio import wait_for_events
from .enums import FileState, Mandatory, ReturnCode, StepState
from .job import Job
from .reporter import ReporterClient
from .scheduler import Scheduler
from .utils import DBLock, remove_path
from .watcher import Watcher
from .worker import WorkerClient
from .workflow import Workflow

__all__ = ("Runner",)


logger = logging.getLogger(__name__)


@attrs.define
class Runner:
    watcher: Watcher | None = attrs.field(kw_only=True)
    """The watcher instance, used to start the watcher when the runner becomes idle."""

    scheduler: Scheduler = attrs.field(kw_only=True)
    """The scheduler providing jobs to the runner."""

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
        with contextlib.ExitStack() as stack:
            # Start workers in parallel.
            await asyncio.gather(
                *[
                    self._launch_worker(
                        idx,
                        stack.enter_context(open(f".stepup/worker{idx}.log", "w")),
                        stop_event,
                    )
                    for idx in range(self.scheduler.num_workers)
                ]
            )
            self.idle_workers = set(range(self.scheduler.num_workers))
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
        """Run all runnable jobs unless the scheduler is drained while the runner is in progress."""
        await self.reporter.update_step_counts(self.workflow.get_step_counts())
        await self.reporter("PHASE", "run")

        # Get step jobs and run them on the workers.
        while True:
            # Get the next job and send it to workers if there is such a job.
            job = self.scheduler.pop_runnable_job()
            if job is not None:
                await self.send_to_worker(job)
                continue

            # When there is nothing left to do, the runner must stop.
            if (
                self.scheduler.queues_empty
                and len(self.running_worker_tasks) == 0
                and len(self.done_worker_tasks) == 0
            ):
                return

            # If the runner needs to wait, there is time to handle exceptions of done tasks.
            self.handle_done_tasks()

            # After handling done tasks, the runner just waits until the scheduler has a new job,
            # or a task has completed.
            await self.scheduler.changed.wait()
            self.scheduler.changed.clear()

    async def finalize(self):
        """Final steps after the runner has executed a bunch of jobs."""
        async with self.dblock:
            self.returncode = await report_completion(self.workflow, self.scheduler, self.reporter)
        if self.returncode.value != 0:
            await self.reporter("WARNING", "Skipping file cleanup due to incomplete build")
            if await clean_queue(self.scheduler, self.dblock, self.reporter):
                self.returncode |= ReturnCode.RUNNABLE
        elif not self.do_remove_outdated:
            await self.reporter("WARNING", "Skipping file cleanup at user's request (--no-clean)")
        else:
            async with self.dblock:
                self.workflow.clean()
            await remove_outdated_outputs(self.workflow, self.dblock, self.reporter)
        async with self.dblock:
            self.workflow.con.execute("VACUUM")
        await self.reporter.update_step_counts(self.workflow.get_step_counts())
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

    async def _launch_worker(self, idx: int, log: TextIOBase, stop_event: asyncio.Event):
        worker = WorkerClient(
            self.scheduler,
            self.workflow,
            self.dblock,
            self.reporter,
            self.director_socket_path,
            self.show_perf,
            self.explain_rerun,
            idx,
        )
        self.workers.append(worker)
        await worker.boot(log, stop_event)
        await self.reporter("DIRECTOR", f"Launched worker {worker.idx}")

    def _task_done(self, task: asyncio.Task, worker_idx: int):
        job = self.running_worker_tasks.pop(task)
        self.done_worker_tasks[task] = job
        if worker_idx in self.active_workers:
            self.active_workers.discard(worker_idx)
            self.idle_workers.add(worker_idx)
        else:
            raise RuntimeError(f"Worker {worker_idx} not in active workers.")
        self.scheduler.release_pool(job.pool)

    def handle_done_tasks(self):
        """Check whether done tasks raised exceptions and propagate them when found."""
        while len(self.done_worker_tasks) > 0:
            task, job = self.done_worker_tasks.popitem()
            exc = task.exception()
            if exc is not None:
                self.scheduler.drain()

                msg = f"Exception in worker task {task.get_name()}"
                raise RuntimeError(msg) from exc
            job.finalize(task.result(), self.scheduler)
            self.scheduler.changed.set()

    async def stop_workers(self):
        waits = []
        while len(self.idle_workers) > 0:
            worker_idx = self.idle_workers.pop()
            worker = self.workers[worker_idx]
            await worker.shutdown()
            waits.append(worker.close())
        while len(self.active_workers) > 0:
            worker_idx = self.active_workers.pop()
            worker = self.workers[worker_idx]
            await worker.shutdown()
            waits.append(worker.close())
        await asyncio.gather(*waits)

    async def interrupt_workers(self, signal: int):
        await asyncio.gather(
            *[self.workers[worker_idx].interrupt(signal) for worker_idx in self.active_workers]
        )


async def report_completion(
    workflow: Workflow, scheduler: Scheduler, reporter: ReporterClient
) -> ReturnCode:
    """Report parts of the workflow that could not be executed."""
    returncode = ReturnCode(0)
    steps_failed = list(workflow.steps(StepState.FAILED))
    num_failed = len(steps_failed)
    if num_failed > 0:
        returncode |= ReturnCode.FAILED
        await reporter("WARNING", f"{num_failed} step(s) failed.")

    if scheduler.onhold:
        returncode |= ReturnCode.PENDING
        await reporter("WARNING", "Scheduler is put on hold. Not reporting pending steps.")
        return returncode

    steps_pending = list(workflow.steps(StepState.PENDING))
    num_pending = len(steps_pending)
    if num_pending > 0:
        pending_pages = []
        block_lines = []
        for step in steps_pending:
            action, workdir = step.get_action_workdir()
            _, _, block, mandatory, _ = step.properties()
            if mandatory == Mandatory.NO:
                num_pending -= 1
                continue
            if block:
                block_lines.append(step.key())
            if len(block_lines) > 0:
                continue

            lines = [f"Action                {action}"]
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
            for path, state, orphan, amended in step.inp_paths(
                yield_state=True, yield_orphan=True, yield_amended=True
            ):
                path_fmt = f"({path})" if orphan else path
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

            pending_pages.append(("PENDING Step", "\n".join(lines)))

        if num_pending > 0:
            returncode |= ReturnCode.PENDING
            lead = f"{num_pending} step(s) remained pending due to"
            if len(block_lines) > 0:
                block_page = ("Blocked steps", "\n".join(block_lines))
                await reporter("WARNING", f"{lead} blocked steps", [block_page])
            else:
                # Insert pages with orphaned and missing inputs in front.
                orphaned_page = "\n".join(
                    f"{file_state.name:>20s}  {path}"
                    for path, file_state in workflow.orphaned_inp_paths()
                )
                if orphaned_page != "":
                    pending_pages.insert(0, ("Orphaned inputs", orphaned_page))
                missing_page = "\n".join(
                    f"             MISSING  {path}" for path in workflow.missing_paths()
                )
                if missing_page != "":
                    pending_pages.insert(0, ("Missing inputs", missing_page))
                # Finally, report the workflow steps that are pending.
                await reporter("WARNING", f"{lead} incomplete requirements", pending_pages)
    return returncode


async def remove_outdated_outputs(workflow: Workflow, dblock: DBLock, reporter: ReporterClient):
    """Remove outdated outputs from the file system and reset their state in the database."""
    await reporter(
        "DIRECTOR",
        f"Trying to delete {len(workflow.to_be_deleted)} outdated output(s)",
    )
    workflow.to_be_deleted.sort(reverse=True)
    # Remove the files from the file system.
    for path, file_hash in workflow.to_be_deleted:
        if (
            path.endswith("/") or file_hash is None or file_hash.regen(path) == file_hash
        ) and remove_path(path):
            await reporter("CLEAN", path)
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


async def clean_queue(scheduler: Scheduler, dblock: DBLock, reporter: ReporterClient) -> bool:
    """Clean the scheduler queue and make queued steps pending."""
    if scheduler.onhold:
        count = 0
        async with dblock:
            while scheduler.job_queue.qsize() > 0:
                job = scheduler.job_queue.get_nowait()
                job.step.set_state(StepState.PENDING)
                count += 1
        if count > 0:
            await reporter("WARNING", f"Made {count} step(s) in the queue pending.")
            return True
    return False
