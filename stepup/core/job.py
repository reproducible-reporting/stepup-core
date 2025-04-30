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
"""Definition of jobs to be executed by a worker."""

from typing import TYPE_CHECKING

import attrs

if TYPE_CHECKING:
    from .file import FileHash
    from .scheduler import Scheduler
    from .step import Step, StepHash
    from .worker import WorkerClient


__all__ = ("Job", "RunJob", "ValidateAmendedJob")


@attrs.define(frozen=True)
class Job:
    """A job managed by the scheduler."""

    step: "Step" = attrs.field()
    """The step related to this job."""

    _pool: str | None = attrs.field()
    """The pool in which the job should be executed, or None for no restriction."""

    inp_hashes: list[tuple[str, "FileHash"]] = attrs.field()
    """The input hashes of the step, as a list of tuples (name, hash)."""

    env_vars: list[str] = attrs.field()
    """The names of (externally defined) environment variables that are used by the step."""

    step_hash: "StepHash" = attrs.field()
    """The hash of the step if it was previously executed, or None if it was not."""

    @property
    def pool(self) -> str | None:
        """The pool in which the job should be executed, or None for no restriction."""
        raise NotImplementedError

    @property
    def name(self) -> str:
        """A human-readable name for the job."""
        raise NotImplementedError

    def coro(self, worker: "WorkerClient"):
        """Return a coroutine, of which the runner will make an asyncio.Task."""
        raise NotImplementedError

    def finalize(self, result, scheduler: "Scheduler"):
        """Finalize the job after execution, may include scheduling another job."""
        raise NotImplementedError


@attrs.define(frozen=True)
class ValidateAmendedJob(Job):
    """Validate that amended inputs have not changed yet, or schedule for rerun.

    This job checks whether the inputs of a step have changed since the last run,
    in which case the amended inputs may be outdated. When that is the case:
    - The step cannot be skipped and the step hash should be discarded.
    - The amended inputs need to be recreated by running the step.
    """

    @property
    def pool(self) -> str | None:
        # A validation of amended inputs never has pool restrictions.
        # The pool attribute is only used to schedule a real run in the correct
        # pool when a simple replay does not work due to changed inputs etc.
        return None

    @property
    def name(self) -> str:
        return f"VALIDATE: {self.step.label}"

    def coro(self, worker: "WorkerClient"):
        return worker.validate_amended_job(
            self.step, self.inp_hashes, self.env_vars, self.step_hash
        )

    def finalize(self, must_run: bool, scheduler: "Scheduler"):
        """Reschedule the job if it must be executed.

        If it does not need to be executed, do nothing. The job will be rescheduled later
        when amended inputs become available.
        """
        if must_run:
            run_job = RunJob(self.step, self._pool, self.inp_hashes, self.env_vars, None)
            scheduler.job_queue.put_nowait(run_job)
            scheduler.changed.set()


@attrs.define(frozen=True)
class RunJob(Job):
    """Skip or execute a job

    When `step_hash` is set, the job is skipped if that hash is still valid,
    i.e. meaning that inputs, environment variables and output have not changed.
    The calculation of the hash is done by the worker and is not restricted to a pool,
    even if the actual execution of the jobs would be.
    If skipping failed, the job will reschedule itself after setting the step_hash to None.

    When `step_hash` is None, the job is executed in the pool specified by `pool`.
    """

    @property
    def pool(self) -> str | None:
        return self._pool if self.step_hash is None else None

    @property
    def name(self) -> str:
        prefix = "EXECUTE" if self.step_hash is None else "SKIP"
        return f"{prefix}: {self.step.label}"

    def coro(self, worker: "WorkerClient"):
        if self.step_hash is None:
            return worker.execute_job(self.step, self.inp_hashes, self.env_vars)
        return worker.try_skip_job(self.step, self.inp_hashes, self.env_vars, self.step_hash)

    def finalize(self, must_run: bool, scheduler: "Scheduler"):
        if must_run:
            run_job = RunJob(self.step, self._pool, self.inp_hashes, self.env_vars, None)
            scheduler.job_queue.put_nowait(run_job)
            scheduler.changed.set()
