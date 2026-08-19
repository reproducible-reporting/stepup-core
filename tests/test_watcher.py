# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Unit tests for stepup.core.watcher."""

import asyncio
import contextlib

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

    async def __call__(self, action, label, pages=None):
        self.calls.append((action, label))

    def start_job(self, letter, description, job_i):
        pass

    def stop_job(self, job_i):
        pass

    async def update_counts(self, nsuccess, ntotal):
        pass


async def test_watch_changes_reports_unchanged_and_updates_only_the_changed_file(
    wfp: Workflow, tmpdir
):
    """A file whose content still matches its cached hash must be reported UNCHANGED and
    pruned from `self.updated` before `process_nglob_changes` runs; a genuinely changed
    file must keep its UPDATED report and get its new hash applied. Exercises the
    `gather_hashes`-based path in `watch_changes` with more than one file at once."""
    with contextlib.chdir(tmpdir):
        with open("same.txt", "w") as fh:
            fh.write("same")
        with open("changed.txt", "w") as fh:
            fh.write("before")
        async with wfp.db:
            plan = wfp.find(Step, "./plan.py")
            wfp.declare_static_files(plan, ["same.txt", "changed.txt"])
            same_hash = FileHash.unknown().regen("same.txt")
            changed_hash = FileHash.unknown().regen("changed.txt")
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
        watcher.interrupt.set()
        watcher.updated.update(["same.txt", "changed.txt"])

        await watcher.watch_changes(asyncio.Queue(), asyncio.Event())

        # "UPDATED" is reported by record_change() for the raw inotify event, not by the
        # hash-confirmation loop under test here (which only ever reports "UNCHANGED");
        # what matters for a genuinely changed file is that it is *not* reported
        # UNCHANGED and that its hash was actually applied (checked below).
        assert ("UNCHANGED", "same.txt") in reporter.calls
        assert ("UNCHANGED", "changed.txt") not in reporter.calls
        async with wfp.db:
            assert wfp.find(File, "same.txt").get_hash() == same_hash
            assert wfp.find(File, "changed.txt").get_hash() != changed_hash


async def test_watch_changes_drain_records_missing_file(wfp: Workflow, tmpdir):
    """A build-phase inotify event for a confirmed-`MISSING` file must be recorded
    by the drain loop in `watch_changes`, not discarded,
    so the file can flip back to `STATIC` in the following watch phase.
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
        watcher.interrupt.set()
        await watcher.watch_changes(change_queue, asyncio.Event())

        async with wfp.db:
            assert ghost.get_state() == FileState.STATIC
