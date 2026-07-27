# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Unit tests for stepup.core.builder."""

import logging
from types import SimpleNamespace

import pytest

from stepup.core.builder import Builder
from stepup.core.executor import Executor
from stepup.core.reporter import ReporterClient
from stepup.core.scheduler import Scheduler
from stepup.core.step import Step
from stepup.core.workflow import Workflow


def _make_builder(scheduler: Scheduler, workflow: Workflow) -> Builder:
    """Build a `Builder` with dummy collaborators, sufficient for `stop()` tests.

    Mirrors `_make_executor()` in `test_executor.py`: `stop()` only touches
    `executor.interrupt()`, `running_tasks`, `db` and `scheduler`, so the other
    collaborators can be dummies.
    """
    executor = Executor(
        scheduler=None,
        workflow=None,
        db=None,
        reporter=None,
        show_perf=False,
        explain_rerun=False,
        keep_going=False,
        live_progress=False,
        do_joblog=False,
        infra_env={},
    )
    return Builder(
        njob=1,
        watcher=None,
        scheduler=scheduler,
        workflow=workflow,
        db=workflow.db,
        reporter=ReporterClient(),
        live_progress=False,
        executor=executor,
    )


async def test_stop_flushes_pending_durations(wfs: Workflow):
    """`stop()` is the one path that runs whether a build phase ends normally or via an
    uncaught step-task exception (see `director.py`'s `finally` around `gather()`), so it
    is a best-effort rescue point for durations accumulated but not yet flushed by
    `finalize()`."""
    async with wfs.db:
        wfs.define_step(wfs.root, "echo")
        step = wfs.find(Step, "echo")

    scheduler = Scheduler(wfs, db=wfs.db, use_duration=True)
    scheduler.new_durations[step.i] = 2.0  # outside the 10% no-op threshold
    builder = _make_builder(scheduler, wfs)

    await builder.stop()

    assert scheduler.new_durations == {}
    async with wfs.db:
        duration = wfs.db.execute("SELECT duration FROM step WHERE node = ?", (step.i,)).fetchone()[
            0
        ]
    assert duration == 2.0


async def test_stop_swallows_flush_failure(wfs: Workflow, caplog, monkeypatch):
    """A flush failure during `stop()` must not mask whatever error is already being
    unwound, nor block shutdown."""
    scheduler = Scheduler(wfs, db=wfs.db, use_duration=True)

    def _raise_build_completed(self):
        raise RuntimeError("boom")

    # Scheduler is a slotted attrs class, so the replacement method must be patched on the
    # class, not assigned on the instance.
    monkeypatch.setattr(Scheduler, "build_completed", _raise_build_completed)
    builder = _make_builder(scheduler, wfs)

    with caplog.at_level(logging.WARNING, logger="stepup.core.builder"):
        await builder.stop()  # must not raise

    assert "Failed to flush step durations" in caplog.text


class _FakeReporter:
    """Records `start_job()`/`stop_job()` calls instead of sending them anywhere."""

    def __init__(self):
        self.events = []

    async def start_job(self, prefix, description, step_i):
        self.events.append(("start", prefix, description, step_i))

    async def stop_job(self, step_i):
        self.events.append(("stop", step_i))


def _make_job(*, prefix: str, step_i: int, label: str, coro):
    """A minimal stand-in for `Job`, sufficient for `_run_with_progress()`."""
    return SimpleNamespace(prefix=prefix, step=SimpleNamespace(i=step_i, label=label), coro=coro)


def _make_progress_builder(reporter: _FakeReporter) -> Builder:
    return Builder(
        njob=1,
        watcher=None,
        scheduler=None,
        workflow=None,
        db=None,
        reporter=reporter,
        live_progress=False,
        executor=None,
    )


async def test_run_with_progress_brackets_a_successful_job():
    """`start_job`/`stop_job` must fire, in order, around the job's own coroutine."""
    reporter = _FakeReporter()
    builder = _make_progress_builder(reporter)

    async def inner(executor):
        assert ("start", "RUN", "echo hi", 1) in reporter.events
        return "done"

    job = _make_job(prefix="RUN", step_i=1, label="echo hi", coro=inner)

    result = await builder._run_with_progress(job)

    assert result == "done"
    assert reporter.events == [("start", "RUN", "echo hi", 1), ("stop", 1)]


async def test_run_with_progress_still_stops_when_job_raises():
    """`stop_job` must fire even when the job coroutine fails, so a step can never be left
    stuck in the progress bar (this is the guarantee that motivated moving the bracket here
    instead of leaving scattered start/stop calls inside `Executor`)."""
    reporter = _FakeReporter()
    builder = _make_progress_builder(reporter)

    async def inner(executor):
        raise ValueError("boom")

    job = _make_job(prefix="SKP", step_i=2, label="false", coro=inner)

    with pytest.raises(ValueError, match="boom"):
        await builder._run_with_progress(job)

    assert reporter.events == [("start", "SKP", "false", 2), ("stop", 2)]
