# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Definition of jobs to be executed by the executor."""

from time import perf_counter
from typing import TYPE_CHECKING

import attrs

from .utils import write_joblog_record

if TYPE_CHECKING:
    from .executor import Executor
    from .hash import FileHash, StepHash
    from .step import Step


__all__ = ("Job", "RunJob", "ValidateAmendedJob")


@attrs.define(frozen=True)
class Job:
    """A job to be executed by the executor."""

    step: "Step" = attrs.field()
    """The step related to this job."""

    inp_hashes: dict[str, "FileHash"] = attrs.field()
    """The input hashes of the step, keyed by path."""

    env_deps: list[str] = attrs.field()
    """The names of (externally defined) environment variables that are used by the step."""

    step_hash: "StepHash" = attrs.field()
    """The hash of the step if it was previously executed, or None if it was not."""

    create_time: float = attrs.field(factory=perf_counter)
    """The creation time of the job, used for scheduling optimization."""

    job_i: int = attrs.field(kw_only=True)
    """Unique id of this job, assigned by `Scheduler` when the job is created.

    Unlike `step.i`, which stays the same across every (re)attempt of a postponed step, this
    id is unique per job, so RPC calls can be matched to the attempt that made them.
    """

    @property
    def name(self) -> str:
        """A name for the job for logging purposes."""
        return f"{self.prefix}: {self.label}"

    @property
    def label(self) -> str:
        """The description of the job shown in the progress bar, i.e. the step's label."""
        return self.step.label

    @property
    def letter(self) -> str:
        """The single character identifying the kind of job in the progress bar."""
        return self.prefix[0]

    @property
    def prefix(self) -> str:
        """The kind of job, spelled out in full for log lines such as `RUN: echo hi`.

        Only its first character, exposed as `letter`, reaches the progress bar.
        """
        raise NotImplementedError

    def coro(self, executor: "Executor"):
        """Return a coroutine, of which the builder will make an asyncio.Task."""
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
    def prefix(self) -> str:
        return "VALIDATE_AMENDED"

    def coro(self, executor: "Executor"):
        inner = executor.validate_amended_job(
            self.job_i, self.step, self.inp_hashes, self.env_deps, self.step_hash
        )
        return _run_job_with_log(self.job_i, self.name, inner) if executor.write_joblog else inner


@attrs.define(frozen=True)
class RunJob(Job):
    """Skip or execute a job

    When `step_hash` is set, the job is skipped if that hash is still valid,
    i.e. meaning that inputs, environment variables and output have not changed.
    """

    @property
    def prefix(self) -> str:
        return "RUN" if self.step_hash is None else "SKIP"

    def coro(self, executor: "Executor"):
        if self.step_hash is None:
            inner = executor.execute_job(self.job_i, self.step, self.inp_hashes, self.env_deps)
        else:
            inner = executor.try_skip_job(
                self.job_i, self.step, self.inp_hashes, self.env_deps, self.step_hash
            )
        return _run_job_with_log(self.job_i, self.name, inner) if executor.write_joblog else inner


async def _run_job_with_log(job_i: int, description: str, coro):
    """Await `coro`, recording `--joblog` `STARTED`/`ENDED` events around it."""
    write_joblog_record("STARTED", job_i, description)
    try:
        return await coro
    finally:
        write_joblog_record("ENDED", job_i, description)
