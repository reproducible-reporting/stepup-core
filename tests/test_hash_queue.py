# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Unit tests for stepup.core.hash_queue."""

import asyncio
import contextlib

from stepup.core.enums import HashUpdateCause
from stepup.core.executor import Executor
from stepup.core.file import File, FileState
from stepup.core.hash import FileHash
from stepup.core.hash_queue import HashQueue, gather_hashes
from stepup.core.reporter import ReporterClient
from stepup.core.workflow import Workflow


async def test_submit_dedups_by_path():
    hash_queue = HashQueue(wake=asyncio.Event())

    job1 = hash_queue.submit("foo.txt", FileHash.unknown(), HashUpdateCause.EXTERNAL)
    job2 = hash_queue.submit("foo.txt", FileHash.unknown(), HashUpdateCause.CONFIRMED)

    assert job1 is job2
    # The first submitter wins: the second call's cause must be ignored.
    assert job1.cause == HashUpdateCause.EXTERNAL
    assert hash_queue.in_flight == {"foo.txt": job1}


async def test_submit_sets_wake_and_assigns_negative_disjoint_ids():
    wake = asyncio.Event()
    hash_queue = HashQueue(wake=wake)

    job1 = hash_queue.submit("foo.txt", FileHash.unknown(), HashUpdateCause.EXTERNAL)
    job2 = hash_queue.submit("bar.txt", FileHash.unknown(), HashUpdateCause.EXTERNAL)

    assert wake.is_set()
    assert job1.job_i < 0
    assert job2.job_i < 0
    assert job1.job_i != job2.job_i


async def test_in_flight_entry_removed_on_success():
    hash_queue = HashQueue(wake=asyncio.Event())
    job = hash_queue.submit("foo.txt", FileHash.unknown(), HashUpdateCause.EXTERNAL)

    job.future.set_result(FileHash.unknown())
    await asyncio.sleep(0)  # let the done callback run

    assert "foo.txt" not in hash_queue.in_flight
    # A later submit() for the same path must start a fresh job, not reuse the old one.
    job2 = hash_queue.submit("foo.txt", FileHash.unknown(), HashUpdateCause.EXTERNAL)
    assert job2 is not job


async def test_in_flight_entry_removed_on_exception():
    hash_queue = HashQueue(wake=asyncio.Event())
    job = hash_queue.submit("foo.txt", FileHash.unknown(), HashUpdateCause.EXTERNAL)

    job.future.set_exception(ValueError("boom"))
    await asyncio.sleep(0)

    assert "foo.txt" not in hash_queue.in_flight
    # Silence the "exception never retrieved" warning for the test's own future.
    assert isinstance(job.future.exception(), ValueError)


async def test_in_flight_entry_removed_on_cancellation():
    hash_queue = HashQueue(wake=asyncio.Event())
    job = hash_queue.submit("foo.txt", FileHash.unknown(), HashUpdateCause.EXTERNAL)

    job.future.cancel()
    await asyncio.sleep(0)

    assert "foo.txt" not in hash_queue.in_flight


async def test_claim_races_leave_exactly_one_winner():
    hash_queue = HashQueue(wake=asyncio.Event())
    job = hash_queue.submit("foo.txt", FileHash.unknown(), HashUpdateCause.EXTERNAL)

    assert hash_queue.claim(job) is True
    assert hash_queue.claim(job) is False
    assert job.started is True


def test_pop_nowait_returns_none_when_empty():
    hash_queue = HashQueue(wake=asyncio.Event())
    assert hash_queue.pop_nowait() is None


async def test_pop_nowait_returns_jobs_in_submission_order():
    hash_queue = HashQueue(wake=asyncio.Event())
    job1 = hash_queue.submit("foo.txt", FileHash.unknown(), HashUpdateCause.EXTERNAL)
    job2 = hash_queue.submit("bar.txt", FileHash.unknown(), HashUpdateCause.EXTERNAL)

    assert hash_queue.pop_nowait() is job1
    assert hash_queue.pop_nowait() is job2
    assert hash_queue.pop_nowait() is None


async def test_pop_nowait_skips_a_job_already_claimed_by_promotion():
    """A Phase 4 direct runner may claim a job before the regular consumer pops it; the
    queue consumer must then silently skip it instead of running it a second time."""
    hash_queue = HashQueue(wake=asyncio.Event())
    job1 = hash_queue.submit("foo.txt", FileHash.unknown(), HashUpdateCause.EXTERNAL)
    job2 = hash_queue.submit("bar.txt", FileHash.unknown(), HashUpdateCause.EXTERNAL)

    assert hash_queue.claim(job1) is True  # simulate promotion

    assert hash_queue.pop_nowait() is job2
    assert hash_queue.pop_nowait() is None


async def test_shutdown_cancels_queued_futures_and_drains_queue():
    hash_queue = HashQueue(wake=asyncio.Event())
    job1 = hash_queue.submit("foo.txt", FileHash.unknown(), HashUpdateCause.EXTERNAL)
    job2 = hash_queue.submit("bar.txt", FileHash.unknown(), HashUpdateCause.EXTERNAL)

    hash_queue.shutdown()

    assert job1.future.cancelled()
    assert job2.future.cancelled()
    assert hash_queue.pop_nowait() is None


async def test_shutdown_does_not_cancel_an_already_started_job():
    hash_queue = HashQueue(wake=asyncio.Event())
    job = hash_queue.submit("foo.txt", FileHash.unknown(), HashUpdateCause.EXTERNAL)
    assert hash_queue.claim(job) is True  # simulate a promoted, already-running job

    hash_queue.shutdown()

    assert not job.future.cancelled()


#
# gather_hashes
#


def _make_executor(workflow: Workflow) -> Executor:
    return Executor(
        scheduler=None,
        workflow=workflow,
        db=workflow.db,
        reporter=ReporterClient(),
        show_perf=False,
        explain_rerun=False,
        keep_going=False,
        live_progress=False,
        do_joblog=False,
        infra_env={},
    )


async def test_gather_hashes_returns_results_in_input_order_and_applies_them(wfs: Workflow, tmpdir):
    with contextlib.chdir(tmpdir):
        async with wfs.db:
            wfs.declare_unconfirmed(wfs.root, ["a.txt", "b.txt"])
        with open("a.txt", "w") as fh:
            fh.write("aaa")
        with open("b.txt", "w") as fh:
            fh.write("bbbb")

        hash_queue = HashQueue(wake=asyncio.Event())
        executor = _make_executor(wfs)
        path_hash_causes = [
            ("a.txt", FileHash.unknown(), HashUpdateCause.CONFIRMED),
            ("b.txt", FileHash.unknown(), HashUpdateCause.CONFIRMED),
        ]

        result = await gather_hashes(
            hash_queue, executor, ReporterClient(), path_hash_causes, njob=2
        )

        assert [path for path, _ in result] == ["a.txt", "b.txt"]
        async with wfs.db:
            assert wfs.find(File, "a.txt").get_state() == FileState.STATIC
            assert wfs.find(File, "b.txt").get_state() == FileState.STATIC


async def test_gather_hashes_respects_njob_concurrency_bound():
    """A fake executor whose `run_hash_job` yields control mid-flight, so overlapping calls
    would show up as a higher `max_concurrent` than `njob` allows."""
    concurrent = 0
    max_concurrent = 0

    class _FakeExecutor:
        async def run_hash_job(self, job):
            nonlocal concurrent, max_concurrent
            concurrent += 1
            max_concurrent = max(max_concurrent, concurrent)
            await asyncio.sleep(0)
            job.future.set_result(FileHash.unknown())
            concurrent -= 1

    hash_queue = HashQueue(wake=asyncio.Event())
    path_hash_causes = [
        (f"f{i}.txt", FileHash.unknown(), HashUpdateCause.EXTERNAL) for i in range(5)
    ]

    await gather_hashes(hash_queue, _FakeExecutor(), ReporterClient(), path_hash_causes, njob=2)

    assert max_concurrent <= 2


async def test_gather_hashes_tolerates_a_duplicate_path_and_runs_it_once():
    """Two entries for the same path dedup to a single `HashJob` (see `HashQueue.submit`);
    the second `run_one` must not run it again, only await the shared future."""
    calls = []

    class _FakeExecutor:
        async def run_hash_job(self, job):
            calls.append(job.path)
            job.future.set_result(FileHash.unknown())

    hash_queue = HashQueue(wake=asyncio.Event())
    path_hash_causes = [
        ("a.txt", FileHash.unknown(), HashUpdateCause.EXTERNAL),
        ("a.txt", FileHash.unknown(), HashUpdateCause.EXTERNAL),
    ]

    result = await gather_hashes(
        hash_queue, _FakeExecutor(), ReporterClient(), path_hash_causes, njob=2
    )

    assert calls == ["a.txt"]
    assert result == [("a.txt", FileHash.unknown()), ("a.txt", FileHash.unknown())]
