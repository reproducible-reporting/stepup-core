# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Unit tests for stepup.core.executor"""

import asyncio
import signal
import threading
from types import SimpleNamespace

import pytest

from stepup.core.enums import StepState
from stepup.core.exceptions import HashCancelledError
from stepup.core.executor import Executor, NoOverwriteDict, Run
from stepup.core.hash import StepHash, compute_inp_hashes
from stepup.core.run import ThreadWorker
from stepup.core.step import Step
from stepup.core.workflow import Workflow


def _make_executor(*, reporter=None, scheduler=None, db=None, keep_going=False) -> Executor:
    """Build an `Executor` with dummy collaborators, sufficient for `report()` tests."""
    return Executor(
        scheduler=scheduler,
        workflow=None,
        db=db,
        reporter=reporter,
        show_perf=False,
        explain_rerun=False,
        keep_going=keep_going,
        live_progress=False,
        do_joblog=False,
        infra_env={},
    )


class _FakeReporter:
    """Records `report()` calls instead of sending them anywhere."""

    def __init__(self):
        self.calls = []

    async def __call__(self, action, label, pages):
        self.calls.append((action, label, pages))


class _NullDB:
    """A no-op stand-in for `DBSession`, sufficient for `report()`'s `async with self.db:`."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False


def _make_detached_run(*, success: bool) -> Run:
    step = SimpleNamespace(i=1, label="one", command_workdir=("echo hi", "."))
    run = Run(step, job_i=1)
    run.success = success
    run.detached = True
    return run


def _make_failed_run() -> Run:
    step = SimpleNamespace(
        i=1, label="false", command_workdir=("false", "."), get_subshell=lambda: None
    )
    run = Run(step, job_i=1)
    run.success = False
    return run


def test_report_marks_detached_step_that_succeeded_as_detached():
    reporter = _FakeReporter()
    executor = _make_executor(reporter=reporter)
    run = _make_detached_run(success=True)

    asyncio.run(executor._report_run(run))

    action, _label, pages = reporter.calls[0]
    assert action == "DETACHED"
    assert pages[0][0] == "Step detached"


def test_report_marks_detached_step_that_failed_as_detached():
    reporter = _FakeReporter()
    executor = _make_executor(reporter=reporter)
    run = _make_detached_run(success=False)

    asyncio.run(executor._report_run(run))

    action, _label, pages = reporter.calls[0]
    assert action == "DETACHED"
    assert pages[0][0] == "Step detached"


def test_report_puts_scheduler_on_hold_after_failure_by_default():
    reporter = _FakeReporter()
    scheduler = SimpleNamespace(on_hold=False)
    executor = _make_executor(reporter=reporter, scheduler=scheduler, db=_NullDB())
    run = _make_failed_run()

    asyncio.run(executor._report_run(run))

    action, _label, _pages = reporter.calls[0]
    assert action == "FAIL"
    assert scheduler.on_hold is True


def test_report_leaves_scheduler_running_with_keep_going():
    reporter = _FakeReporter()
    scheduler = SimpleNamespace(on_hold=False)
    executor = _make_executor(reporter=reporter, scheduler=scheduler, db=_NullDB(), keep_going=True)
    run = _make_failed_run()

    asyncio.run(executor._report_run(run))

    action, _label, _pages = reporter.calls[0]
    assert action == "FAIL"
    assert scheduler.on_hold is False


def _make_worker_run(job_i: int) -> Run:
    step = SimpleNamespace(i=1, label="./step.sh")
    return Run(step, job_i=job_i)


class _WorkCancelledError(Exception):
    """Raised by a fake `work` callable to simulate reacting to `cancel_event`."""


async def testrun_work_thread_happy_path():
    executor = _make_executor()
    run = _make_worker_run(11)

    def work(cancel_event):
        return "done"

    result = await executor._run_work_thread(run, work)

    assert result == "done"
    assert run.worker is None
    assert 11 not in executor.running


async def testrun_work_thread_interrupt():
    started = threading.Event()

    def work(cancel_event):
        started.set()
        assert cancel_event.wait(10)
        raise _WorkCancelledError

    executor = _make_executor()
    run = _make_worker_run(12)
    task = asyncio.ensure_future(executor._run_work_thread(run, work))
    await asyncio.to_thread(started.wait, 10)

    # Exercises the ThreadWorker branch in interrupt(); the signal value is ignored there.
    executor.interrupt(signal.SIGTERM)

    with pytest.raises(_WorkCancelledError):
        await task
    assert run.worker is None
    assert 12 not in executor.running


async def testrun_work_thread_exception():
    def work(cancel_event):
        raise ValueError("boom")

    executor = _make_executor()
    run = _make_worker_run(13)

    with pytest.raises(ValueError, match="boom"):
        await executor._run_work_thread(run, work)
    assert run.worker is None
    assert 13 not in executor.running


def test_no_overwrite_dict_allows_insertion_of_new_keys():
    d = NoOverwriteDict()
    d[1] = "a"
    d[2] = "b"
    assert d[1] == "a"
    assert d[2] == "b"
    with pytest.raises(KeyError):
        d[2] = "c"


#
# Hash cancellation during shutdown must fail the step gracefully, not crash the job loop
# (see the `except HashCancelledError` clause in `Executor._run_work_thread`, shared by
# `_new_run`, `_compute_out_step_hash`, and `_compute_full_step_hash`).
#


async def _raise_hash_cancelled(self):
    raise HashCancelledError("/some/path")


async def testnew_run_cancelled_reports_failure_instead_of_raising(wfs: Workflow, monkeypatch):
    async with wfs.db:
        wfs.define_step(wfs.root, "echo hi")
        step = wfs.find(Step, "echo hi")

    monkeypatch.setattr(ThreadWorker, "run_in_thread", _raise_hash_cancelled)
    reporter = _FakeReporter()
    scheduler = SimpleNamespace(on_hold=False, record_stop_time=lambda step_i, *, succeeded: None)
    executor = _make_executor(reporter=reporter, scheduler=scheduler, db=wfs.db)

    run, new_hash = await executor._new_run(1, step, [], [])

    assert new_hash is None
    assert run.success is False
    assert "cancelled" in run.stderr
    async with wfs.db:
        assert step.get_state() == StepState.FAILED
    assert reporter.calls[-1][0] == "FAIL"


async def test_compute_out_step_hash_cancelled_reports_failure(wfs: Workflow, monkeypatch):
    async with wfs.db:
        wfs.define_step(wfs.root, "echo hi")
        step = wfs.find(Step, "echo hi")

    monkeypatch.setattr(ThreadWorker, "run_in_thread", _raise_hash_cancelled)
    reporter = _FakeReporter()
    scheduler = SimpleNamespace(on_hold=False, record_stop_time=lambda step_i, *, succeeded: None)
    executor = _make_executor(reporter=reporter, scheduler=scheduler, db=wfs.db)
    run = Run(step, job_i=1)
    step_hash = StepHash.from_inp(step.label, False, [], {})

    new_hash, new_out_hashes = await executor._compute_out_step_hash(run, step_hash)

    assert new_hash is None
    assert new_out_hashes == []
    assert run.success is False
    assert "cancelled" in run.stderr


async def test_compute_full_step_hash_cancelled_returns_sentinel_without_raising(
    wfs: Workflow, monkeypatch
):
    """Unlike `_new_run` and `_compute_out_step_hash`, `_compute_full_step_hash` does not
    finalize the run itself: `execute_job` already finalizes unconditionally afterward,
    and does so correctly given `run.success = False` and no new hashes to report."""
    async with wfs.db:
        wfs.define_step(wfs.root, "echo hi")
        step = wfs.find(Step, "echo hi")
        state_before = step.get_state()

    monkeypatch.setattr(ThreadWorker, "run_in_thread", _raise_hash_cancelled)
    reporter = _FakeReporter()
    executor = _make_executor(reporter=reporter, db=wfs.db)
    run = Run(step, job_i=1)

    new_hash, new_inp_hashes, new_out_hashes = await executor._compute_full_step_hash(run)

    assert new_hash is None
    assert new_inp_hashes == []
    assert new_out_hashes == []
    assert run.success is False
    assert "cancelled" in run.stderr
    # _compute_full_step_hash must not finalize the step or report anything itself.
    async with wfs.db:
        assert step.get_state() == state_before
    assert reporter.calls == []


async def test_try_skip_job_bails_out_when_out_hash_cancelled(wfs: Workflow, monkeypatch):
    """End-to-end check that `try_skip_job` does not use a `None` step hash from a cancelled
    `_compute_out_step_hash` call, mirroring the existing check after `_new_run`."""
    async with wfs.db:
        wfs.define_step(wfs.root, "echo hi")
        step = wfs.find(Step, "echo hi")

    orig_run_in_thread = ThreadWorker.run_in_thread

    async def _raise_for_out_hash(self):
        # Let the input-hash computation inside `_new_run` succeed normally, and only
        # cancel the output-hash computation done by `_compute_out_step_hash`.
        if self.work.func is compute_inp_hashes:
            return await orig_run_in_thread(self)
        raise HashCancelledError("/some/output")

    monkeypatch.setattr(ThreadWorker, "run_in_thread", _raise_for_out_hash)
    reporter = _FakeReporter()
    scheduler = SimpleNamespace(on_hold=False, record_stop_time=lambda step_i, *, succeeded: None)
    executor = _make_executor(reporter=reporter, scheduler=scheduler, db=wfs.db)
    step_hash = StepHash.from_inp(step.label, False, [], {})

    # try_skip_job must not raise, even though _compute_out_step_hash is cancelled.
    await executor.try_skip_job(1, step, [], [], step_hash)

    async with wfs.db:
        assert step.get_state() == StepState.FAILED
    assert reporter.calls[-1][0] == "FAIL"
