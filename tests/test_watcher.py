# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Unit tests for stepup.core.watcher."""

import asyncio
import contextlib
import os

from path import Path

from stepup.core.enums import Change, HashUpdateCause
from stepup.core.executor import Executor
from stepup.core.file import File, FileState
from stepup.core.hash import FileHash
from stepup.core.hash_queue import HashQueue
from stepup.core.reporter import ReporterClient
from stepup.core.step import Step
from stepup.core.watcher import Watcher
from stepup.core.workflow import Workflow


def _make_watcher(workflow: Workflow) -> Watcher:
    executor = Executor(
        scheduler=None,
        workflow=workflow,
        db=workflow.db,
        reporter=ReporterClient(),
        explain_rerun=False,
        keep_going=False,
        live_progress=False,
        write_joblog=False,
        infra_env={},
    )
    return Watcher(
        workflow=workflow,
        db=workflow.db,
        reporter=ReporterClient(),
        dir_queue=asyncio.Queue(),
        executor=executor,
        hash_queue=HashQueue(wake=asyncio.Event()),
        njob=1,
    )


class _FakeReporter:
    """Records `report()` calls instead of sending them anywhere."""

    def __init__(self):
        self.calls = []

    async def __call__(self, tag, label, pages=None):
        self.calls.append((tag, label))

    def job_started(self, job_i, letter, description):
        pass

    def job_stopped(self, job_i):
        pass

    async def update_progress(self, ndone, ntotal):
        pass


async def test_run_once_reports_unchanged_and_updates_only_the_changed_file(wfp: Workflow, tmpdir):
    """A file whose content still matches its cached hash must be reported UNCHANGED and
    pruned from `self.updated` before `process_nglob_changes` runs; a genuinely changed
    file must keep its UPDATED report and get its new hash applied. Exercises the
    `gather_hashes`-based path in `run_once` with more than one file at once."""
    with contextlib.chdir(tmpdir):
        with open("same.txt", "w") as fh:
            fh.write("same")
        with open("changed.txt", "w") as fh:
            fh.write("before")
        async with wfp.db:
            plan = wfp.find(Step, "./plan.py")
            wfp.declare_static_files(plan, ["same.txt", "changed.txt"])
            same_hash = FileHash.unknown().refreshed("same.txt")
            changed_hash = FileHash.unknown().refreshed("changed.txt")
            wfp.update_file_hashes(
                {"same.txt": same_hash, "changed.txt": changed_hash},
                cause=HashUpdateCause.CONFIRMED,
            )

        # Simulate "changed.txt" having been rewritten while the build phase was active,
        # and both paths having been recorded as (candidate) updates by the watcher.
        with open("changed.txt", "w") as fh:
            fh.write("after")

        watcher = _make_watcher(wfp)
        reporter = _FakeReporter()
        watcher.reporter = reporter
        watcher.end_watching.set()
        watcher.updated.update(["same.txt", "changed.txt"])

        await watcher.run_once(asyncio.Queue())

        # "UPDATED" is reported by record_change() for the raw inotify event, not by the
        # hash-confirmation loop under test here (which only ever reports "UNCHANGED");
        # what matters for a genuinely changed file is that it is *not* reported
        # UNCHANGED and that its hash was actually applied (checked below).
        assert ("UNCHANGED", "same.txt") in reporter.calls
        assert ("UNCHANGED", "changed.txt") not in reporter.calls
        async with wfp.db:
            assert wfp.find(File, "same.txt").get_hash() == same_hash
            assert wfp.find(File, "changed.txt").get_hash() != changed_hash


async def test_run_once_drain_records_missing_file(wfp: Workflow, tmpdir):
    """A build-phase inotify event for a confirmed-`MISSING` file must be recorded
    by the drain loop in `run_once`, not discarded,
    so the file can flip back to `CONFIRMED` in the following watch phase.
    """
    with contextlib.chdir(tmpdir):
        async with wfp.db:
            plan = wfp.find(Step, "./plan.py")
            wfp.declare_static_files(plan, ["ghost.txt"])
            wfp.update_file_hashes(
                {"ghost.txt": FileHash.unknown()}, cause=HashUpdateCause.CONFIRMED
            )
            ghost = wfp.find(File, "ghost.txt")
            assert ghost.get_state() == FileState.MISSING

        # Simulate the file being created externally while the build phase is still
        # running: the inotify event lands in change_queue before the drain loop at the
        # start of the next watch phase gets to it.
        with open("ghost.txt", "w") as fh:
            fh.write("hello")
        change_queue = asyncio.Queue()
        change_queue.put_nowait((Change.UPDATED, "ghost.txt"))

        watcher = _make_watcher(wfp)
        watcher.end_watching.set()
        await watcher.run_once(change_queue)

        async with wfp.db:
            assert ghost.get_state() == FileState.CONFIRMED


async def test_run_once_drain_records_files_under_removed_dir(wfp: Workflow, tmpdir):
    """A `DELETED_PARENT` event queued during the build phase must reach the files under it.

    StepUp has no node for the directory itself,
    so a removed directory can only be judged through the files recorded under it.
    """
    with contextlib.chdir(tmpdir):
        os.mkdir("sub")
        with open("sub/a.txt", "w") as fh:
            fh.write("hello")
        async with wfp.db:
            plan = wfp.find(Step, "./plan.py")
            wfp.declare_static_files(plan, ["sub/a.txt"])
            wfp.update_file_hashes(
                {"sub/a.txt": FileHash.unknown().refreshed("sub/a.txt")},
                cause=HashUpdateCause.CONFIRMED,
            )

        # Simulate the directory being removed while the build phase was still running.
        os.remove("sub/a.txt")
        os.rmdir("sub")
        change_queue = asyncio.Queue()
        change_queue.put_nowait((Change.DELETED_PARENT, Path("sub")))

        watcher = _make_watcher(wfp)
        watcher.end_watching.set()
        await watcher.run_once(change_queue)

        async with wfp.db:
            assert wfp.find(File, "sub/a.txt").get_state() == FileState.MISSING


async def test_record_deleted_parent_skips_build_products_during_build(wfp: Workflow, tmpdir):
    """A step removing its own output directory is not news, so nothing is reported.

    The same removal in the watch phase is news,
    because no step is running then to have caused it.
    """
    with contextlib.chdir(tmpdir):
        os.mkdir("sub")
        with open("sub/out.txt", "w") as fh:
            fh.write("built")
        async with wfp.db:
            plan = wfp.find(Step, "./plan.py")
            wfp.define_step(plan, "prog", out_paths=["sub/out.txt"])
            wfp.update_file_hashes(
                {"sub/out.txt": FileHash.unknown().refreshed("sub/out.txt")},
                cause=HashUpdateCause.SUCCEEDED,
            )
            assert wfp.find(File, "sub/out.txt").get_state() == FileState.BUILT

        # Simulate the step cleaning up its output directory before regenerating it.
        os.remove("sub/out.txt")
        os.rmdir("sub")
        watcher = _make_watcher(wfp)
        reporter = _FakeReporter()
        watcher.reporter = reporter
        async with wfp.db:
            await watcher.record_change(Change.DELETED_PARENT, Path("sub"), during_build=True)
        assert watcher.deleted == set()
        assert reporter.calls == []

        async with wfp.db:
            await watcher.record_change(Change.DELETED_PARENT, Path("sub"))
        assert watcher.deleted == {"sub/out.txt"}
        assert reporter.calls == [("DELETED", "sub/out.txt")]
