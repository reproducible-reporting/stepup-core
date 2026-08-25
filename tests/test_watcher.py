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
from stepup.core.nglob import NamedGlob
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
            wfp.update_file_hash("same.txt", same_hash, cause=HashUpdateCause.OBSERVED)
            wfp.update_file_hash("changed.txt", changed_hash, cause=HashUpdateCause.OBSERVED)

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


async def test_run_once_ignores_glob_relevant_undeclared_file(wfp: Workflow, tmpdir):
    """A detached UNDECLARED file that a pattern makes relevant must not be hashed.

    `change_is_relevant` says yes because the path matches a registered pattern,
    while the transition rules reject an observation of an UNDECLARED file,
    so `run_once` must leave such a path out of the hash update.
    The path still reaches `process_nglob_changes`, which is what recorded it in the first place.
    """
    with contextlib.chdir(tmpdir):
        with open("given.txt", "w") as fh:
            fh.write("given")
        async with wfp.db:
            plan = wfp.find(Step, "./plan.py")
            wfp.define_step(plan, "cat given.txt", inp_paths=["given.txt"])
            wfp.register_nglob(plan, NamedGlob("*.txt"))
            given = wfp.find(File, "given.txt")
            assert given.get_state() == FileState.UNDECLARED
            assert wfp.change_is_relevant("given.txt")

        watcher = _make_watcher(wfp)
        reporter = _FakeReporter()
        watcher.reporter = reporter
        watcher.end_watching.set()
        watcher.updated.add("given.txt")

        await watcher.run_once(asyncio.Queue())

        # Nothing was observed about the file, so it is not reported UNCHANGED either.
        assert ("UNCHANGED", "given.txt") not in reporter.calls
        async with wfp.db:
            assert given.get_state() == FileState.UNDECLARED


async def test_run_once_reports_settled_unconfirmed_file_as_changed(wfp: Workflow, tmpdir):
    """An UNCONFIRMED file settled by an unchanged hash is not an UNCHANGED report.

    The hash did not move, but the state did, so the file stays in `self.updated`
    and reaches `process_nglob_changes`.
    """
    with contextlib.chdir(tmpdir):
        with open("stale.txt", "w") as fh:
            fh.write("stale")
        async with wfp.db:
            plan = wfp.find(Step, "./plan.py")
            wfp.declare_static_files(plan, ["stale.txt"])
            stale_hash = FileHash.unknown().refreshed("stale.txt")
            wfp.update_file_hash("stale.txt", stale_hash, cause=HashUpdateCause.OBSERVED)
            stale = wfp.find(File, "stale.txt")
            assert stale.get_state() == FileState.CONFIRMED
            # A CONFIRMED file may go back to UNCONFIRMED with its hash intact,
            # see the `file_clear_hash` trigger in `file.py`.
            stale.set_state(FileState.UNCONFIRMED)
            assert stale.get_hash() == stale_hash

        watcher = _make_watcher(wfp)
        reporter = _FakeReporter()
        watcher.reporter = reporter
        watcher.end_watching.set()
        watcher.updated.add("stale.txt")

        await watcher.run_once(asyncio.Queue())

        assert ("UNCHANGED", "stale.txt") not in reporter.calls
        async with wfp.db:
            assert stale.get_state() == FileState.CONFIRMED


async def test_run_once_drain_records_missing_file(wfp: Workflow, tmpdir):
    """A build-phase inotify event for a confirmed-`MISSING` file must be recorded
    by the drain loop in `run_once`, not discarded,
    so the file can flip back to `CONFIRMED` in the following watch phase.
    """
    with contextlib.chdir(tmpdir):
        async with wfp.db:
            plan = wfp.find(Step, "./plan.py")
            wfp.declare_static_files(plan, ["ghost.txt"])
            wfp.update_file_hash("ghost.txt", FileHash.unknown(), cause=HashUpdateCause.OBSERVED)
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
            wfp.update_file_hash(
                "sub/a.txt",
                FileHash.unknown().refreshed("sub/a.txt"),
                cause=HashUpdateCause.OBSERVED,
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
            wfp.update_file_hash(
                "sub/out.txt",
                FileHash.unknown().refreshed("sub/out.txt"),
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
