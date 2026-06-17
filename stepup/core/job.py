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
"""Definition of jobs to be executed by a worker."""

from time import perf_counter
from typing import TYPE_CHECKING

import attrs

if TYPE_CHECKING:
    from .file import FileHash
    from .step import Step, StepHash
    from .worker import WorkerClient


__all__ = ("Job", "RunJob", "ValidateAmendedJob")


@attrs.define(frozen=True)
class Job:
    """A job to be executed by a worker."""

    step: "Step" = attrs.field()
    """The step related to this job."""

    inp_hashes: list[tuple[str, "FileHash"]] = attrs.field()
    """The input hashes of the step, as a list of tuples (name, hash)."""

    env_vars: list[str] = attrs.field()
    """The names of (externally defined) environment variables that are used by the step."""

    step_hash: "StepHash" = attrs.field()
    """The hash of the step if it was previously executed, or None if it was not."""

    create_time: float = attrs.field(factory=perf_counter)
    """The creation time of the job, used for scheduling optimization."""

    @property
    def name(self) -> str:
        """A human-readable name for the job."""
        raise NotImplementedError

    def coro(self, worker: "WorkerClient"):
        """Return a coroutine, of which the runner will make an asyncio.Task."""
        raise NotImplementedError

    def duration(self) -> float | None:
        """Return the duration of the job since the creation time."""
        return perf_counter() - self.create_time


@attrs.define(frozen=True)
class ValidateAmendedJob(Job):
    """Validate that amended inputs have not changed yet, or enforce a full rerun.

    This job checks whether the inputs of a step have changed since the last run,
    in which case the amended inputs may be outdated. When that is the case:
    - The step cannot be skipped and the step hash should be discarded.
    - The amended inputs need to be recreated by running the step.
    """

    @property
    def name(self) -> str:
        return f"VALIDATE: {self.step.label}"

    def coro(self, worker: "WorkerClient"):
        return worker.validate_amended_job(
            self.step, self.inp_hashes, self.env_vars, self.step_hash
        )


@attrs.define(frozen=True)
class RunJob(Job):
    """Skip or execute a job

    When `step_hash` is set, the job is skipped if that hash is still valid,
    i.e. meaning that inputs, environment variables and output have not changed.
    """

    @property
    def name(self) -> str:
        prefix = "EXECUTE" if self.step_hash is None else "SKIP"
        return f"{prefix}: {self.step.label}"

    def coro(self, worker: "WorkerClient"):
        if self.step_hash is None:
            return worker.execute_job(self.step, self.inp_hashes, self.env_vars)
        return worker.try_skip_job(self.step, self.inp_hashes, self.env_vars, self.step_hash)
