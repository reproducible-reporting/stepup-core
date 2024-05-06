# StepUp Core provides the basic framework for the StepUp build tool.
# Copyright (C) 2024 Toon Verstraelen
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
"""The `Runner` delegates the execution of jobs by the workers."""

import asyncio
import logging
from functools import partial

import attrs
from path import Path

from .asyncio import wait_for_events
from .hash import FileHash
from .job import Job
from .reporter import ReporterClient
from .scheduler import Scheduler
from .step import Mandatory, StepState
from .utils import remove_path
from .watcher import Watcher
from .worker import WorkerClient
from .workflow import Workflow

__all__ = ("Runner",)


@attrs.define
class Runner:
    # The watcher instance, used to start the watcher when the runner becomes idle.
    watcher: Watcher = attrs.field()

    # The scheduler providing jobs to the runner.
    scheduler: Scheduler = attrs.field()

    # The workflow which generated the jobs and which gets updated as a result of the jobs.
    workflow: Workflow = attrs.field()

    # The location of the workflow file (is written after the runner becomes idle).
    path_workflow: Path = attrs.field()

    # A reporter client for sending progress info to.
    reporter: ReporterClient = attrs.field()

    # The path of the director socket, passed on to worker processes.
    director_socket_path: str = attrs.field()

    # Flag to enable performance output after a worker executed a step.
    show_perf: bool = attrs.field()

    # Flag to enable more details on why steps cannot be skipped.
    explain_rerun: bool = attrs.field()

    # Other parts of StepUp can set the resume event to put the runner back to work.
    resume: asyncio.Event = attrs.field(init=False, factory=asyncio.Event)

    # A list of worker client objects, one for each worker process.
    workers: list[WorkerClient] = attrs.field(init=False, factory=list)

    # The list of active and idle workers (just integer indexes of the workers list).
    active_workers: set[int] = attrs.field(init=False, factory=set)
    idle_workers: set[int] = attrs.field(init=False, factory=set)

    # Dictionary of asyncio tasks that interact with a worker client.
    running_worker_tasks: dict[asyncio.Task, Job] = attrs.field(init=False, factory=dict)
    done_worker_tasks: dict[asyncio.Task, Job] = attrs.field(init=False, factory=dict)

    # Flag set when an error is raised halfway an RPC that changes the workflow.
    # StepUp has no rollback mechanism, so this is the nuclear option to prevent
    # StepUp from continuing with a half-baked workflow.
    dissolve_after_dump: bool = attrs.field(init=False, default=False)

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
        # Start workers
        self.idle_workers.update(
            await asyncio.gather(
                *[self._launch_worker() for _ in range(self.scheduler.num_workers)]
            )
        )
        # Loop through runner phases.
        while True:
            await self.job_loop()
            await self.finalize()
            self.resume.clear()
            await wait_for_events(self.resume, stop_event, return_when=asyncio.FIRST_COMPLETED)
            if stop_event.is_set():
                return

    async def job_loop(self):
        """Run all runnable jobs unless the scheduler is drained while the runner is in progress."""
        await self.reporter.update_step_counts(self.workflow.get_step_counters())
        await self.reporter("PHASE", "run")

        # Get step jobs and run them on the workers.
        while True:
            # Get the next job and send it to workers if there is such a job.
            job, pool_name = self.scheduler.pop_runnable_job()
            if job is not None:
                await self.send_to_worker(job, pool_name)
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
        success = await report_completion(self.workflow, self.scheduler, self.reporter)
        if success:
            self.workflow.clean()
            await remove_files(self.workflow.to_be_deleted, self.reporter)
        else:
            await self.reporter("WARNING", "Skipping cleanup due to incomplete build.")
        self.workflow.to_file(self.path_workflow)
        await self.reporter.update_step_counts(self.workflow.get_step_counters())
        await self.reporter("WORKFLOW", f"Dumped to {self.path_workflow}")
        if self.dissolve_after_dump:
            await self.reporter(
                "WARNING",
                "Dissolving the workflow due to an exceptions while the graph was being changed.",
            )
            self.workflow.dissolve()
            self.dissolve_after_dump = False
        self.watcher.resume.set()

    async def send_to_worker(self, job: Job, pool_name: str):
        if len(self.idle_workers) > 0:
            worker_idx = self.idle_workers.pop()
        else:
            # Normally never needed because workers are pre-launched, but keeping to play safe.
            worker_idx = await self._launch_worker()
        worker = self.workers[worker_idx]
        self.active_workers.add(worker_idx)
        task = asyncio.create_task(job.coro(worker), name=job.name)
        self.running_worker_tasks[task] = job
        task.add_done_callback(partial(self._task_done, worker_idx=worker_idx))

    async def _launch_worker(self) -> int:
        worker = WorkerClient(
            self.workflow,
            self.reporter,
            self.director_socket_path,
            self.show_perf,
            self.explain_rerun,
            len(self.workers),
        )
        self.workers.append(worker)
        await worker.boot()
        await self.reporter("DIRECTOR", f"Launched worker {worker.idx}")
        return worker.idx

    def _task_done(self, task: asyncio.Task, worker_idx: int):
        job = self.running_worker_tasks.pop(task)
        self.done_worker_tasks[task] = job
        self.active_workers.discard(worker_idx)
        self.idle_workers.add(worker_idx)
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
            job.finalize(task.result(), self.scheduler, self.workflow)
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


async def remove_files(to_be_deleted: list[tuple[str, FileHash | None]], reporter: ReporterClient):
    to_be_deleted.sort(reverse=True)
    for path, file_hash in to_be_deleted:
        if (
            path.endswith("/") or file_hash is None or file_hash.update(path) is False
        ) and remove_path(Path(path)):
            await reporter("CLEAN", path)
    to_be_deleted.clear()


async def report_completion(workflow: Workflow, scheduler: Scheduler, reporter: ReporterClient):
    """Report parts of the workflow that could not be executed."""
    success = True
    step_keys_failed = workflow.step_states.inverse.get(StepState.FAILED, ())
    step_keys_failed = [
        step_key for step_key in step_keys_failed if not workflow.is_orphan(step_key)
    ]
    num_failed = len(step_keys_failed)
    if num_failed > 0:
        success = False
        for step_key in step_keys_failed:
            logging.error(f"Step FAILED: {step_key}")
        await reporter("WARNING", f"{num_failed} step(s) failed, see error messages above")

    if scheduler.onhold:
        await reporter("WARNING", "Scheduler is put on hold. Not reporting pending steps.")
        return False

    step_keys_pending = workflow.step_states.inverse.get(StepState.PENDING, ())
    step_keys_pending = [
        step_key for step_key in step_keys_pending if not workflow.is_orphan(step_key)
    ]
    num_pending = len(step_keys_pending)
    if num_pending > 0:
        pending_pages = []
        block_lines = []
        for step_key in sorted(step_keys_pending):
            step = workflow.get_step(step_key)
            if step.get_mandatory(workflow) == Mandatory.NO:
                num_pending -= 1
                continue
            if step.block:
                block_lines.append(step.key)
            if len(block_lines) > 0:
                continue

            lines = [f"Command               {step.command}"]
            if step.workdir != ".":
                lines.append(f"Working directory     {step.workdir}")

            static_paths = step.get_static_paths(workflow, state=True)
            prefix = "Declares"
            for path, state in static_paths:
                lines.append(f"{prefix}      {state.name:>8s}  {path}")
                prefix = "        "

            inp_paths = step.get_inp_paths(workflow, state=True, orphan=True)
            prefix = "Inputs"
            for path, state, orphan in inp_paths:
                path_fmt = f"({path})" if orphan else path
                lines.append(f"{prefix}      {state.name:>8s}  {path_fmt}")
                prefix = "      "

            out_paths = step.get_out_paths(workflow, state=True)
            prefix = "Outputs"
            for path, state in out_paths:
                lines.append(f"{prefix}     {state.name:>8s}  {path}")
                prefix = "       "

            vol_paths = step.get_vol_paths(workflow)
            for path in vol_paths:
                lines.append(f"{prefix}     VOLATILE  {path}")
                prefix = "       "

            pending_pages.append(("PENDING Step", "\n".join(lines)))

        if num_pending > 0:
            success = False
            lead = "1 step remains" if num_pending == 1 else f"{num_pending} steps remain"
            if len(block_lines) > 0:
                block_page = ("Blocked steps", "\n".join(block_lines))
                await reporter("WARNING", f"{lead} pending due to blocked steps", [block_page])
            else:
                await reporter(
                    "WARNING", f"{lead} pending due to incomplete requirements", pending_pages
                )
    return success
