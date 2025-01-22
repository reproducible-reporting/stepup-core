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
    from .scheduler import Scheduler
    from .step import Step
    from .worker import WorkerClient


__all__ = ("ExecuteJob", "Job", "SetPoolJob", "TrySkipJob", "ValidateAmendedJob")


@attrs.define(frozen=True)
class Job:
    @property
    def pool(self) -> str | None:
        raise NotImplementedError

    @property
    def name(self) -> str:
        raise NotImplementedError

    def coro(self, worker: "WorkerClient"):
        """Return a coroutine, of which the runner will make an asyncio.Task."""
        raise NotImplementedError

    def finalize(self, result, scheduler: "Scheduler"):
        raise NotImplementedError


@attrs.define(frozen=True)
class SetPoolJob(Job):
    """This is a stub: the scheduler uses it to set the pool, never executes on the worker."""

    _pool: str = attrs.field()
    _size: int = attrs.field()

    @property
    def pool(self) -> str | None:
        # Setting a pool never requires a pool.
        return None

    @property
    def set_pool_args(self) -> tuple[str, int]:
        return (self._pool, self._size)


@attrs.define(frozen=True)
class ValidateAmendedJob(Job):
    """Validate that amended inputs have not changed yet, or schedule for rerun."""

    _step: "Step" = attrs.field()
    _pool: str | None = attrs.field()

    @property
    def pool(self) -> str | None:
        # A validation of amended inputs never has pool restrictions.
        # The pool attribute is only used to schedule a real run in the correct
        # pool when a simple replay does not work due to changed inputs etc.
        return None

    @property
    def name(self) -> str:
        return f"ValidateAmended {self._step.label}"

    def coro(self, worker: "WorkerClient"):
        return worker.validate_amended_job(self._step)

    def finalize(self, must_run: bool, scheduler: "Scheduler"):
        if must_run:
            run_job = ExecuteJob(self._step, self._pool)
            scheduler.inqueue.put_nowait(run_job)
            scheduler.changed.set()


@attrs.define(frozen=True)
class TrySkipJob(Job):
    """Check if the outputs of the step are still valid. If so, just skip the execution.

    The two main difference with a ExecuteJob are:
    1. The job is not actually executed.
    2. The job is not scheduled to run in a pool.

    The second point is the reason skipping a step and running a step are not done in a single job.
    This way, skipping jobs does not congest any pool.
    """

    _step: "Step" = attrs.field()
    _pool: str | None = attrs.field()

    @property
    def pool(self) -> str | None:
        # A skip never has pool restrictions.
        return None

    @property
    def name(self) -> str:
        return f"TrySkip {self._step.label}"

    def coro(self, worker: "WorkerClient"):
        return worker.try_skip_job(self._step)

    def finalize(self, must_run: bool, scheduler: "Scheduler"):
        if must_run:
            run_job = ExecuteJob(self._step, self._pool)
            scheduler.inqueue.put_nowait(run_job)
            scheduler.changed.set()


@attrs.define(frozen=True)
class ExecuteJob(Job):
    """Actually execute a job."""

    _step: "Step" = attrs.field()
    _pool: str | None = attrs.field()

    @property
    def pool(self) -> str | None:
        return self._pool

    @property
    def name(self) -> str:
        return f"Execute {self._step.label}"

    def coro(self, worker: "WorkerClient"):
        return worker.run_job(self._step)

    def finalize(self, _, scheduler: "Scheduler"):
        # When a run job has completed, it does not need to create a follow-up job.
        # This is the last possible job for a given step to run.
        pass
