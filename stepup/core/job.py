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
"""Definition of jobs to be executed by a worker."""

from typing import TYPE_CHECKING

import attrs

if TYPE_CHECKING:
    from .scheduler import Scheduler
    from .worker import WorkerClient
    from .workflow import Workflow


__all__ = ("Job", "SetPoolJob", "ValidateAmendedJob", "TryReplayJob", "RunJob")


@attrs.define
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

    def finalize(self, result, scheduler: "Scheduler", workflow: "Workflow"):
        raise NotImplementedError


@attrs.define
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


@attrs.define
class ValidateAmendedJob(Job):
    """Validate that amended inputs have not changed yet, or schedule for rerun."""

    _step_key: str = attrs.field()
    _pool: str | None = attrs.field()

    @property
    def pool(self) -> str | None:
        # A validation of amended inputs never has pool restrictions.
        # The pool attribute is only used to schedule a real run in the correct
        # pool when a simple replay does not work due to changed inputs etc.
        return None

    @property
    def name(self) -> str:
        return f"validate amended {self._step_key}"

    def coro(self, worker: "WorkerClient"):
        return worker.validate_amended_job(self._step_key)

    def finalize(self, must_run: bool, scheduler: "Scheduler", workflow: "Workflow"):
        if must_run:
            run_job = RunJob(self._step_key, self._pool)
            scheduler.inqueue.put_nowait(run_job)
            scheduler.changed.set()


@attrs.define
class TryReplayJob(Job):
    """Simulate the execution of a job, if inputs and outputs have not changed."""

    _step_key: str = attrs.field()
    _pool: str | None = attrs.field()

    @property
    def pool(self) -> str | None:
        # A replay never has pool restrictions.
        # The pool attribute is only used to schedule a real run in the correct
        # pool when a simple replay does not work due to changed inputs etc.
        return None

    @property
    def name(self) -> str:
        return f"replay {self._step_key}"

    def coro(self, worker: "WorkerClient"):
        return worker.try_replay_job(self._step_key)

    def finalize(self, must_run: bool, scheduler: "Scheduler", workflow: "Workflow"):
        if must_run:
            run_job = RunJob(self._step_key, self._pool)
            scheduler.inqueue.put_nowait(run_job)
            scheduler.changed.set()


@attrs.define
class RunJob(Job):
    """Actually execute a job."""

    _step_key: str = attrs.field()
    _pool: str | None = attrs.field()

    @property
    def pool(self) -> str | None:
        return self._pool

    @property
    def name(self) -> str:
        return f"run {self._step_key}"

    def coro(self, worker: "WorkerClient"):
        return worker.run_job(self._step_key)

    def finalize(self, _, scheduler: "Scheduler", workflow: "Workflow"):
        # When a run job has completed, it does not need to create a follow-up job.
        # This is the last possible job for a given step to run.
        pass
