# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Unit tests for stepup.core.builder."""

import asyncio
import contextlib
import logging
from types import SimpleNamespace

import pytest
from conftest import get_duration_and_tail_time
from path import Path

from stepup.core.builder import Builder
from stepup.core.enums import HashUpdateCause, Need, StepState
from stepup.core.executor import Executor
from stepup.core.file import File, FileState
from stepup.core.hash import FileHash, StepHash
from stepup.core.reporter import ReporterClient
from stepup.core.scheduler import Scheduler
from stepup.core.step import Step
from stepup.core.workflow import Workflow


def _make_builder(
    scheduler: Scheduler, workflow: Workflow, do_remove_outdated: bool = True
) -> Builder:
    """Build a `Builder` with dummy collaborators, sufficient for `stop()` and `finalize()`.

    Mirrors `_make_executor()` in `test_executor.py`: neither method touches the executor
    beyond `interrupt()`, so the other collaborators can be dummies.
    """
    executor = Executor(
        scheduler=None,
        workflow=None,
        db=None,
        reporter=None,
        explain_rerun=False,
        keep_going=False,
        live_progress=False,
        write_joblog=False,
        infra_env={},
    )
    return Builder(
        njob=1,
        scheduler=scheduler,
        workflow=workflow,
        db=workflow.db,
        reporter=ReporterClient(),
        live_progress=False,
        executor=executor,
        do_remove_outdated=do_remove_outdated,
    )


async def test_stop_flushes_pending_durations(wfp: Workflow):
    """`stop()` is the one path that runs whether a build phase ends normally or via an
    uncaught step-task exception (see `director.py`'s `finally` around `gather()`), so it
    is a best-effort rescue point for durations accumulated but not yet flushed by
    `finalize()`."""
    scheduler = Scheduler(wfp, db=wfp.db, use_duration=True)
    await scheduler.initialize(None)

    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "foo", out_paths=["data.txt"], duration=2.0)
        foo = wfp.find(Step, "foo")
        wfp.define_step(plan, "bar", inp_paths=["data.txt"])
        bar = wfp.find(Step, "bar")

    scheduler.new_durations[bar.i] = 3.0
    builder = _make_builder(scheduler, wfp)

    await builder.stop()

    assert scheduler.new_durations == {}
    duration, tail_time, check_after = await get_duration_and_tail_time(wfp.db, foo)
    assert duration == 2.0
    assert tail_time == 5.0
    assert check_after == 0
    duration, tail_time, check_after = await get_duration_and_tail_time(wfp.db, bar)
    assert duration == 3.0
    assert tail_time == 3.0
    assert check_after == 0


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


@pytest.mark.parametrize("do_remove_outdated", [True, False])
async def test_finalize_reverts_optional_only_when_cleaning(
    wfp: Workflow, tmpdir, do_remove_outdated: bool
):
    """`revert_optional` must happen if and only if the outdated outputs are removed too.

    Reverting resets the outputs of the optional step in the database and queues their paths
    in `to_be_deleted`. Without the removal that follows it, the database would claim the
    outputs are gone while the files are still there, and the queued paths would survive into
    a later build phase, which can no longer tell whether they still refer to the same nodes.
    """
    with contextlib.chdir(tmpdir):
        for path in "data.txt", "vol.log":
            with open(path, "w") as fh:
                fh.write(path)

        scheduler = Scheduler(wfp, db=wfp.db)
        await scheduler.initialize(None)
        async with wfp.db:
            plan = wfp.find(Step, "./plan.py")
            # The plan step must succeed as well: a pending step anywhere makes the build
            # incomplete, which is itself a reason to skip the cleanup.
            plan.mark_completed(StepHash(b"ppp", None, b"qqq", None), False)
            wfp.define_step(
                plan, "foo", out_paths=["data.txt"], vol_paths=["vol.log"], need=Need.OPTIONAL
            )
            foo = wfp.find(Step, "foo")
            wfp.update_file_hashes(
                {"data.txt": FileHash.unknown().refreshed("data.txt")},
                cause=HashUpdateCause.SUCCEEDED,
            )
            foo.mark_completed(StepHash(b"aaa", None, b"zzz", None), False)
            assert foo.get_state() == StepState.SUCCEEDED
            assert wfp.find(File, "data.txt").get_state() == FileState.BUILT

        builder = _make_builder(scheduler, wfp, do_remove_outdated=do_remove_outdated)
        await builder.finalize()

        # Either way, nothing may be left queued for a later build phase to delete blindly.
        assert wfp.to_be_deleted == {}
        async with wfp.db:
            if do_remove_outdated:
                assert foo.get_state() == StepState.PENDING
                assert wfp.find(File, "data.txt").get_state() == FileState.PLANNED
                assert not Path("data.txt").exists()
                assert not Path("vol.log").exists()
            else:
                assert foo.get_state() == StepState.SUCCEEDED
                assert wfp.find(File, "data.txt").get_state() == FileState.BUILT
                assert Path("data.txt").exists()
                assert Path("vol.log").exists()


class _FakeReporter:
    """Records `job_started()`/`job_stopped()` calls instead of sending them anywhere."""

    def __init__(self):
        self.events = []

    def job_started(self, step_i: int, letter: str, description: str):
        self.events.append(("start", step_i, letter, description))

    def job_stopped(self, step_i: int):
        self.events.append(("stop", step_i))


def _make_job(*, letter: str, job_i: int, label: str, coro):
    """A minimal stand-in for `Job`, sufficient for `_run_with_progress()`."""
    return SimpleNamespace(letter=letter, job_i=job_i, label=label, coro=coro)


def _make_progress_builder(reporter: _FakeReporter) -> Builder:
    return Builder(
        njob=1,
        scheduler=None,
        workflow=None,
        db=None,
        reporter=reporter,
        live_progress=False,
        executor=None,
    )


async def test_run_with_progress_brackets_a_successful_job():
    """`job_started`/`job_stopped` must fire, in order, around the job's own coroutine."""
    reporter = _FakeReporter()
    builder = _make_progress_builder(reporter)

    async def inner(executor):
        assert ("start", 1, "R", "echo hi") in reporter.events
        return "done"

    job = _make_job(letter="R", job_i=1, label="echo hi", coro=inner)

    result = await builder._run_with_progress(job)

    assert result == "done"
    assert reporter.events == [("start", 1, "R", "echo hi"), ("stop", 1)]


async def test_run_with_progress_still_stops_when_job_raises():
    """`job_stopped` must fire even when the job coroutine fails, so a step can never be left
    stuck in the progress bar (this is the guarantee that motivated moving the bracket here
    instead of leaving scattered start/stop calls inside `Executor`)."""
    reporter = _FakeReporter()
    builder = _make_progress_builder(reporter)

    async def inner(executor):
        raise ValueError("boom")

    job = _make_job(letter="S", job_i=2, label="false", coro=inner)

    with pytest.raises(ValueError, match="boom"):
        await builder._run_with_progress(job)

    assert reporter.events == [("start", 2, "S", "false"), ("stop", 2)]


async def test_job_loop_dispatches_hash_jobs_before_runnable_steps(wfs: Workflow, monkeypatch):
    """Hash jobs jump the SQL-poll queue: with both a hash job and a runnable step
    pending, the hash job must be dispatched first."""
    async with wfs.db:
        wfs.define_step(wfs.root, "echo hi")

    scheduler = Scheduler(wfs, db=wfs.db)
    await scheduler.initialize(None)
    executor = Executor(
        scheduler=scheduler,
        workflow=wfs,
        db=wfs.db,
        reporter=ReporterClient(),
        explain_rerun=False,
        keep_going=False,
        live_progress=False,
        write_joblog=False,
        infra_env={},
    )
    builder = Builder(
        njob=1,
        scheduler=scheduler,
        workflow=wfs,
        db=wfs.db,
        reporter=ReporterClient(),
        live_progress=False,
        executor=executor,
    )

    dispatched = []

    def fake_start_task(self, job):
        dispatched.append(("step", job))

    def fake_start_hash_task(self, hash_job):
        dispatched.append(("hash", hash_job))

    monkeypatch.setattr(Builder, "start_task", fake_start_task)
    monkeypatch.setattr(Builder, "start_hash_task", fake_start_hash_task)

    hash_job = builder.hash_queue.submit("foo.txt", FileHash.unknown(), HashUpdateCause.EXTERNAL)

    await builder.job_loop()

    assert [kind for kind, _job in dispatched] == ["hash", "step"]
    assert dispatched[0][1] is hash_job


#
# Builder.run_promoted_hash_jobs
#


async def test_run_promoted_hash_jobs_applies_result(wfs: Workflow, tmpdir):
    """A promoted hash job (Phase 4's `amend()` blocking) must run and apply its result to
    the workflow, same as one drained normally from the queue."""
    with contextlib.chdir(tmpdir):
        async with wfs.db:
            wfs.declare_static_files(wfs.root, ["a.txt"])
        with open("a.txt", "w") as fh:
            fh.write("aaa")

        scheduler = Scheduler(wfs, db=wfs.db)
        executor = Executor(
            scheduler=scheduler,
            workflow=wfs,
            db=wfs.db,
            reporter=ReporterClient(),
            explain_rerun=False,
            keep_going=False,
            live_progress=False,
            write_joblog=False,
            infra_env={},
        )
        builder = Builder(
            njob=1,
            scheduler=scheduler,
            workflow=wfs,
            db=wfs.db,
            reporter=ReporterClient(),
            live_progress=False,
            executor=executor,
        )

        await builder.run_promoted_hash_jobs(
            {"a.txt": FileHash.unknown()}, HashUpdateCause.CONFIRMED
        )

        async with wfs.db:
            assert wfs.find(File, "a.txt").get_state() == FileState.CONFIRMED


async def test_run_promoted_hash_jobs_awaits_already_claimed_job_without_rerunning():
    """When another runner (the regular queue consumer, or a concurrent promotion for the
    same path) already claimed the job, `run_promoted_hash_jobs` must only await the shared
    future, not run it a second time."""
    calls = []
    reporter = _FakeReporter()
    builder = _make_progress_builder(reporter)

    async def fake_run_hash_job(job):
        calls.append(job.path)
        job.future.set_result(FileHash.unknown())

    builder.executor = SimpleNamespace(run_hash_job=fake_run_hash_job)
    job = builder.hash_queue.submit("foo.txt", FileHash.unknown(), HashUpdateCause.CONFIRMED)
    assert builder.hash_queue.claim(job) is True  # simulate another runner already claimed it

    async def resolve_soon():
        # Resolve only after run_promoted_hash_jobs has started awaiting the shared future,
        # so its own submit() still finds the job in_flight (an immediate set_result() here
        # would fire the done-callback that evicts it before submit() even runs).
        await asyncio.sleep(0)
        job.future.set_result(FileHash.unknown())

    resolver = asyncio.create_task(resolve_soon())
    try:
        await builder.run_promoted_hash_jobs(
            {"foo.txt": FileHash.unknown()}, HashUpdateCause.CONFIRMED
        )
    finally:
        await resolver

    assert calls == []
    assert reporter.events == []
