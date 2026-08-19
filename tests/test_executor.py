# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Unit tests for stepup.core.executor"""

import asyncio
import contextlib
import signal
import threading
from types import SimpleNamespace

import pytest
from path import Path

from stepup.core.enums import HashUpdateCause, Need, StepState
from stepup.core.exceptions import HashCancelledError
from stepup.core.executor import Executor, NoOverwriteDict, Run
from stepup.core.file import File, FileState
from stepup.core.hash import FileHash, StepHash, compute_inp_hashes
from stepup.core.hash_queue import HashJob
from stepup.core.outcome import ChildOutcome, ResourceUsage
from stepup.core.run import ThreadWorker, Worker
from stepup.core.step import Step
from stepup.core.workflow import Workflow


def _make_executor(
    *, reporter=None, scheduler=None, workflow=None, db=None, keep_going=False
) -> Executor:
    """Build an `Executor` with dummy collaborators, sufficient for `report()` tests."""
    return Executor(
        scheduler=scheduler,
        workflow=workflow,
        db=db,
        reporter=reporter,
        explain_rerun=False,
        keep_going=keep_going,
        live_progress=False,
        write_joblog=False,
        infra_env={},
    )


class _FakeReporter:
    """Records `report()` and progress-bar calls instead of sending them anywhere."""

    def __init__(self):
        self.calls = []
        self.jobs = []

    async def __call__(self, action, label, pages=None):
        self.calls.append((action, label, pages))

    def start_job(self, letter, label, job_i):
        self.jobs.append(("start", letter, label, job_i))

    def stop_job(self, job_i):
        self.jobs.append(("stop", job_i))


class _NullDB:
    """A no-op stand-in for `DBSession`, sufficient for `report()`'s `async with self.db:`."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False


def _make_failed_run() -> Run:
    step = SimpleNamespace(
        i=1, label="false", command_and_workdir=("false", "."), uses_shell=lambda: None
    )
    run = Run(step, job_i=1)
    run.success = False
    run.outcome = ChildOutcome(1, "", "")
    return run


def test_report_drains_scheduler_after_failure_by_default():
    reporter = _FakeReporter()
    scheduler = SimpleNamespace(draining=False)
    executor = _make_executor(reporter=reporter, scheduler=scheduler, db=_NullDB())
    run = _make_failed_run()

    asyncio.run(executor._report_run(run))

    action, _label, _pages = reporter.calls[0]
    assert action == "FAIL"
    assert scheduler.draining is True


def test_report_leaves_scheduler_running_with_keep_going():
    reporter = _FakeReporter()
    scheduler = SimpleNamespace(draining=False)
    executor = _make_executor(reporter=reporter, scheduler=scheduler, db=_NullDB(), keep_going=True)
    run = _make_failed_run()

    asyncio.run(executor._report_run(run))

    action, _label, _pages = reporter.calls[0]
    assert action == "FAIL"
    assert scheduler.draining is False


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
        raise _WorkCancelledError("boom")

    executor = _make_executor()
    run = _make_worker_run(12)
    task = asyncio.ensure_future(executor._run_work_thread(run, work))
    await asyncio.to_thread(started.wait, 10)

    # Exercises the ThreadWorker branch in interrupt(); the signal value is ignored there.
    executor.interrupt(signal.SIGTERM)

    # Must not propagate as a task exception: any error from the work callable fails
    # just this run, instead of crashing the job loop (see testrun_work_thread_exception).
    result = await task
    assert result is None
    assert run.success is False
    assert "boom" in run.outcome.stderr
    assert run.worker is None
    assert 12 not in executor.running


async def testrun_work_thread_exception():
    def work(cancel_event):
        raise ValueError("boom")

    executor = _make_executor()
    run = _make_worker_run(13)

    result = await executor._run_work_thread(run, work)

    assert result is None
    assert run.success is False
    assert "boom" in run.outcome.stderr
    assert run.worker is None
    assert 13 not in executor.running


class _FakeProcessWorker(Worker):
    """A worker that records the signals it is sent instead of touching a process."""

    def __init__(self, job_i: int):
        super().__init__(job_i=job_i)
        self.calls = []

    def _describe(self) -> str:
        return "fake process"

    def _signal(self, sig: int) -> None:
        self.calls.append(sig)


def test_suspend_resume_signal_process_workers():
    """Steps are stopped with `SIGSTOP` and continued with `SIGCONT`."""
    executor = _make_executor()
    run = _make_worker_run(21)
    worker = _FakeProcessWorker(21)
    run.worker = worker
    executor.running[21] = run

    executor.suspend()
    assert worker.calls == [signal.SIGSTOP]
    nrun, seconds = executor.resume()
    assert worker.calls == [signal.SIGSTOP, signal.SIGCONT]
    assert nrun == 1
    assert seconds >= 0.0
    assert executor.suspended_total == seconds


async def test_suspend_leaves_hash_threads_alone():
    """A `ThreadWorker` runs inside the director, which is suspended as a whole."""
    executor = _make_executor()
    run = _make_worker_run(22)
    run.worker = ThreadWorker(job_i=22, work=lambda cancel_event: None)
    executor.running[22] = run

    executor.suspend()
    executor.resume()

    # Unlike interrupt(), suspension must not cancel the computation.
    assert not run.worker._cancel_event.is_set()


def test_suspend_is_idempotent_and_resume_needs_a_suspend():
    """A second suspension does not restart the clock, and a stray resume does nothing."""
    executor = _make_executor()
    assert executor.resume() == (0, 0.0)

    executor.suspend()
    first_start = executor._suspend_start
    executor.suspend()
    assert executor._suspend_start == first_start

    executor.resume()
    assert executor._suspend_start is None
    total = executor.suspended_total
    assert executor.resume() == (0, 0.0)
    assert executor.suspended_total == total


def _make_command_run(job_i: int) -> Run:
    """Build a `Run` whose step answers everything `_run_command` asks of it."""
    step = SimpleNamespace(
        i=1,
        label="./step.sh",
        command_and_workdir=("./step.sh", Path(".")),
        uses_shell=lambda: False,
        get_need=lambda: Need.DEFAULT,
        get_env_overrides=dict,
        out_paths=lambda: (),
        vol_paths=lambda: (),
    )
    return Run(step, job_i=job_i)


@pytest.mark.parametrize(
    ("wtime", "expected"), [(10.0, 5.0), (1.0, 0.0)], ids=["discounted", "clamped"]
)
async def test_run_command_discounts_suspended_time(
    monkeypatch: pytest.MonkeyPatch, wtime: float, expected: float
):
    """Time spent suspended is not recorded as time the step spent working.

    The second case checks the clamp at zero, which the `CHECK(wtime >= 0)` constraint
    on the `step_outcome` table depends on.
    """
    workflow = SimpleNamespace(create_dirs=lambda paths: None)
    executor = _make_executor(reporter=_FakeReporter(), db=_NullDB(), workflow=workflow)
    run = _make_command_run(23)

    async def fake_launch_command(*args, **kwargs):
        executor.suspend()
        # Pretend the suspension lasted 5 seconds instead of waiting for it.
        executor._suspend_start -= 5.0
        executor.resume()
        return ChildOutcome(0, "", "", ResourceUsage(wtime=wtime))

    monkeypatch.setattr("stepup.core.executor.launch_command", fake_launch_command)
    await executor._run_command(run)

    assert executor.suspended_total >= 5.0
    assert run.outcome.usage.wtime == pytest.approx(max(0.0, wtime - executor.suspended_total))
    assert run.outcome.usage.wtime == pytest.approx(expected, abs=0.1)


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
    scheduler = SimpleNamespace(draining=False, record_stop_time=lambda step_i, *, succeeded: None)
    executor = _make_executor(reporter=reporter, scheduler=scheduler, db=wfs.db)

    run, new_hash = await executor._new_run(1, step, [], [])

    assert new_hash is None
    assert run.success is False
    assert "cancelled" in run.outcome.stderr
    async with wfs.db:
        assert step.get_state() == StepState.FAILED
    assert reporter.calls[-1][0] == "FAIL"


async def test_compute_out_step_hash_cancelled_reports_failure(wfs: Workflow, monkeypatch):
    async with wfs.db:
        wfs.define_step(wfs.root, "echo hi")
        step = wfs.find(Step, "echo hi")

    monkeypatch.setattr(ThreadWorker, "run_in_thread", _raise_hash_cancelled)
    reporter = _FakeReporter()
    scheduler = SimpleNamespace(draining=False, record_stop_time=lambda step_i, *, succeeded: None)
    executor = _make_executor(reporter=reporter, scheduler=scheduler, db=wfs.db)
    run = Run(step, job_i=1)
    step_hash = StepHash.from_inp(step.label, False, {}, {})

    new_hash, new_out_hashes = await executor._compute_out_step_hash(run, step_hash)

    assert new_hash is None
    assert new_out_hashes == {}
    assert run.success is False
    assert "cancelled" in run.outcome.stderr


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
    assert new_inp_hashes == {}
    assert new_out_hashes == {}
    assert run.success is False
    assert "cancelled" in run.outcome.stderr
    # _compute_full_step_hash must not finalize the step or report anything itself.
    async with wfs.db:
        assert step.get_state() == state_before
    assert reporter.calls == []


@pytest.mark.parametrize(
    ("stderr_before", "separator"),
    [("warning\n", "\n"), ("", "")],
)
async def test_compute_full_step_hash_cancelled_keeps_command_outcome(
    wfs: Workflow, monkeypatch, stderr_before: str, separator: str
):
    """A cancellation after the command ran must not discard what the command produced.

    `_compute_full_step_hash` is the only `_run_work_thread` call site that runs after
    `_run_command` (see `execute_job`), so `run.outcome` already holds the child's real
    return code, output and resource usage. The cancellation note is appended to its
    stderr instead of replacing the whole outcome, which would otherwise report and
    persist an empty stdout and a fabricated `returncode=1` in `step_outcome`.
    The separating newline is only added when there is stderr to separate it from.
    """
    async with wfs.db:
        wfs.define_step(wfs.root, "echo hi")
        step = wfs.find(Step, "echo hi")

    monkeypatch.setattr(ThreadWorker, "run_in_thread", _raise_hash_cancelled)
    executor = _make_executor(reporter=_FakeReporter(), db=wfs.db)
    run = Run(step, job_i=1)
    usage = ResourceUsage(utime=1.0, stime=0.5, wtime=2.0)
    run.outcome = ChildOutcome(0, "hi\n", stderr_before, usage)

    new_hash, new_inp_hashes, new_out_hashes = await executor._compute_full_step_hash(run)

    assert new_hash is None
    assert new_inp_hashes == {}
    assert new_out_hashes == {}
    assert run.success is False
    # The command's own outcome survives, only stderr gains the cancellation note.
    assert run.outcome.returncode == 0
    assert run.outcome.stdout == "hi\n"
    assert run.outcome.usage == usage
    assert run.outcome.stderr == (
        stderr_before + separator + "Hash computation was cancelled because the build is shutting "
        "down."
    )


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
    scheduler = SimpleNamespace(draining=False, record_stop_time=lambda step_i, *, succeeded: None)
    executor = _make_executor(reporter=reporter, scheduler=scheduler, db=wfs.db)
    step_hash = StepHash.from_inp(step.label, False, {}, {})

    # try_skip_job must not raise, even though _compute_out_step_hash is cancelled.
    await executor.try_skip_job(1, step, [], [], step_hash)

    async with wfs.db:
        assert step.get_state() == StepState.FAILED
    assert reporter.calls[-1][0] == "FAIL"


#
# Executor.run_hash_job
#


def _spy_update_file_hashes(monkeypatch) -> list:
    """Patch `Workflow.update_file_hashes` to record calls while still applying them.

    Workflow is a slotted attrs class (like Scheduler, see `test_stop_swallows_flush_failure`
    in test_builder.py), so the replacement must be patched on the class, not the instance.
    """
    calls = []
    orig = Workflow.update_file_hashes

    def spy(self, file_hashes, *, cause):
        calls.append((dict(file_hashes), cause))
        return orig(self, file_hashes, cause=cause)

    monkeypatch.setattr(Workflow, "update_file_hashes", spy)
    return calls


async def test_run_hash_job_confirmed_applies_even_when_unchanged(
    wfs: Workflow, tmpdir, monkeypatch
):
    """CONFIRMED must be applied even when the hash didn't change: it's the only cause that
    flips UNCONFIRMED -> CONFIRMED."""
    with contextlib.chdir(tmpdir):
        async with wfs.db:
            wfs.declare_static_files(wfs.root, ["foo.txt"])
        with open("foo.txt", "w") as fh:
            fh.write("hello")
        real_hash = FileHash.unknown().regen("foo.txt")

        calls = _spy_update_file_hashes(monkeypatch)
        executor = _make_executor(reporter=_FakeReporter(), workflow=wfs, db=wfs.db)
        hash_job = HashJob("foo.txt", real_hash, HashUpdateCause.CONFIRMED, -1)

        await executor.run_hash_job(hash_job)

        assert calls == [({"foo.txt": real_hash}, HashUpdateCause.CONFIRMED)]
        assert hash_job.future.result() == real_hash
        async with wfs.db:
            assert wfs.find(File, "foo.txt").get_state() == FileState.CONFIRMED


async def test_run_hash_job_external_not_applied_when_unchanged(wfs: Workflow, tmpdir, monkeypatch):
    """An unchanged hash under a non-CONFIRMED cause must not trigger update_file_hashes:
    e.g. (EXTERNAL, CONFIRMED, known) would call handle_external_update and needlessly mark
    all sinks pending."""
    with contextlib.chdir(tmpdir):
        async with wfs.db:
            wfs.declare_static_files(wfs.root, ["foo.txt"])
        with open("foo.txt", "w") as fh:
            fh.write("hello")
        real_hash = FileHash.unknown().regen("foo.txt")
        async with wfs.db:
            wfs.update_file_hashes({"foo.txt": real_hash}, cause=HashUpdateCause.CONFIRMED)
            assert wfs.find(File, "foo.txt").get_state() == FileState.CONFIRMED

        calls = _spy_update_file_hashes(monkeypatch)
        executor = _make_executor(reporter=_FakeReporter(), workflow=wfs, db=wfs.db)
        hash_job = HashJob("foo.txt", real_hash, HashUpdateCause.EXTERNAL, -1)

        await executor.run_hash_job(hash_job)

        assert calls == []
        assert hash_job.future.result() == real_hash


async def test_run_hash_job_brackets_progress_bar(wfs: Workflow, tmpdir):
    """Hash jobs are user-visible progress items, using the `H` letter and
    `HashJob.job_i`/`.path` in place of `Step.i`/`.label`. The bracket lives in
    `run_hash_job` because hash jobs are started from three different places."""
    with contextlib.chdir(tmpdir):
        with open("foo.txt", "w") as fh:
            fh.write("hello")
        async with wfs.db:
            wfs.declare_static_files(wfs.root, ["foo.txt"])
        reporter = _FakeReporter()
        executor = _make_executor(reporter=reporter, workflow=wfs, db=wfs.db)
        hash_job = HashJob("foo.txt", FileHash.unknown(), HashUpdateCause.CONFIRMED, -1)

        await executor.run_hash_job(hash_job)

    assert reporter.jobs == [("start", "H", "foo.txt", -1), ("stop", -1)]


async def test_run_hash_job_stops_progress_bar_when_it_raises(monkeypatch):
    """Even an error that `run_hash_job` does not handle must not leave a dangling
    `start_job` in the progress bar."""

    async def _boom(self, hash_job):
        raise ValueError("boom")

    monkeypatch.setattr(Executor, "_run_hash_job", _boom)
    reporter = _FakeReporter()
    executor = _make_executor(reporter=reporter)
    hash_job = HashJob("foo.txt", FileHash.unknown(), HashUpdateCause.EXTERNAL, -2)

    with pytest.raises(ValueError, match="boom"):
        await executor.run_hash_job(hash_job)

    assert reporter.jobs == [("start", "H", "foo.txt", -2), ("stop", -2)]


async def test_run_hash_job_cancelled_cancels_future_without_raising(monkeypatch):
    def _raise_cancelled(old_hash, path, cancel_event=None):
        raise HashCancelledError(path)

    monkeypatch.setattr(FileHash, "regen", _raise_cancelled)
    executor = _make_executor(reporter=_FakeReporter())
    hash_job = HashJob("foo.txt", FileHash.unknown(), HashUpdateCause.EXTERNAL, -1)

    await executor.run_hash_job(hash_job)  # must not raise

    assert hash_job.future.cancelled()


async def test_run_hash_job_exception_resolves_future_without_raising(wfs: Workflow, monkeypatch):
    """A stat error (e.g. a permission problem) must resolve the future with the exception,
    not propagate: an exception escaping a builder task crashes job_loop via
    handle_done_tasks. It must also drain the scheduler: a fire-and-forget submitter
    (static/step) never awaits this future, so draining's existing
    "stop dispatching new steps" + report_completion warning is what actually surfaces the
    failure to the user instead of it being silently lost."""

    def _raise_permission_error(old_hash, path, cancel_event=None):
        raise PermissionError("denied")

    async with wfs.db:
        wfs.define_step(wfs.root, "cat foo.txt", inp_paths=["foo.txt"])
        wfs.declare_static_files(wfs.root, ["foo.txt"])

    monkeypatch.setattr(FileHash, "regen", _raise_permission_error)
    reporter = _FakeReporter()
    scheduler = SimpleNamespace(draining=False)
    executor = _make_executor(reporter=reporter, scheduler=scheduler, workflow=wfs, db=wfs.db)
    hash_job = HashJob("foo.txt", FileHash.unknown(), HashUpdateCause.EXTERNAL, -1)

    await executor.run_hash_job(hash_job)  # must not raise

    assert isinstance(hash_job.future.exception(), PermissionError)
    action, _label, pages = reporter.calls[-1]
    assert action == "ERROR"
    # The error names the steps involved with the file, so the user can find the plan.py call.
    assert pages[0][0] == "Provenance of foo.txt"
    assert "step:cat foo.txt" in pages[0][1]
    assert scheduler.draining is True


async def test_run_hash_job_exception_without_file_node_reports_no_provenance(
    wfs: Workflow, monkeypatch
):
    """A file that vanished from the workflow while its hash ran still reports the error."""

    def _raise_permission_error(old_hash, path, cancel_event=None):
        raise PermissionError("denied")

    monkeypatch.setattr(FileHash, "regen", _raise_permission_error)
    reporter = _FakeReporter()
    scheduler = SimpleNamespace(draining=False)
    executor = _make_executor(reporter=reporter, scheduler=scheduler, workflow=wfs, db=wfs.db)
    hash_job = HashJob("gone.txt", FileHash.unknown(), HashUpdateCause.EXTERNAL, -1)

    await executor.run_hash_job(hash_job)  # must not raise

    # Retrieve the exception, like `HashQueue._job_done` does in production:
    # otherwise asyncio warns "Future exception was never retrieved" once this
    # test-only future (never routed through `HashQueue`) is garbage collected.
    assert isinstance(hash_job.future.exception(), PermissionError)
    action, _label, pages = reporter.calls[-1]
    assert action == "ERROR"
    assert pages == []
