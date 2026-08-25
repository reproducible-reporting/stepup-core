# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Unit tests for stepup.core.startup."""

import contextlib
import os

from conftest import declare_static

from stepup.core.builder import Builder
from stepup.core.enums import HashUpdateCause, StepState
from stepup.core.executor import Executor
from stepup.core.file import File, FileState
from stepup.core.hash import FileHash, StepHash
from stepup.core.nglob import NamedGlob
from stepup.core.reporter import ReporterClient
from stepup.core.scheduler import Scheduler
from stepup.core.startup import rescan_files, rescan_nglobs, watch_known_dirs
from stepup.core.step import Step
from stepup.core.workflow import Workflow


def _make_stray_unconfirmed(workflow: Workflow, path: str) -> None:
    """Move an already-CONFIRMED file back to UNCONFIRMED, keeping its cached hash.

    A direct DB poke rather than a second `declare_static_files()` call: recycling a
    node's state through the public API requires it to be detached first (e.g. via a
    step's `reset_for_rerun()`), which is more machinery than this test needs. This
    reproduces the on-disk shape of a director killed after redeclaring an
    already-known-good static file but before its confirming hash job completed --
    see `File.initialize_row()` for why the hash column survives such a redeclare.
    """
    workflow.db.execute(
        "UPDATE file SET state = ? WHERE node = (SELECT i FROM node WHERE label = ?)",
        (FileState.UNCONFIRMED.value, path),
    )


def _make_builder(workflow: Workflow) -> Builder:
    scheduler = Scheduler(workflow, db=workflow.db)
    executor = Executor(
        scheduler=scheduler,
        workflow=workflow,
        db=workflow.db,
        reporter=ReporterClient(),
        explain_rerun=False,
        keep_going=False,
        live_progress=False,
        write_joblog=False,
        infra_env={},
    )
    return Builder(
        njob=2,
        scheduler=scheduler,
        workflow=workflow,
        db=workflow.db,
        reporter=ReporterClient(),
        live_progress=False,
        executor=executor,
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


async def test_rescan_files_does_nothing_when_nothing_to_check(wfs: Workflow):
    """An empty/no-eligible-rows scan must not report anything."""
    builder = _make_builder(wfs)
    reporter = _FakeReporter()

    await rescan_files(wfs, reporter, builder)

    assert reporter.calls == []


async def test_rescan_files_confirms_unchanged_stray_unconfirmed_row(wfs: Workflow, tmpdir):
    """A stray UNCONFIRMED row (crash while its settling hash job was still queued or in
    flight) whose cached hash still matches disk must become CONFIRMED directly,
    without depending on a step rerun and without an UPDATED/DELETED report
    (the file did not actually change)."""
    with contextlib.chdir(tmpdir):
        with open("foo.txt", "w") as fh:
            fh.write("hello")
        # A real (not `conftest.fake_hash`) hash is needed here: the "unchanged" case
        # only holds if the cached hash actually matches what refreshed() computes from disk.
        real_hash = FileHash.unknown().refreshed("foo.txt")
        async with wfs.db:
            wfs.declare_static_files(wfs.root, ["foo.txt"])
            wfs.update_file_hash("foo.txt", real_hash, cause=HashUpdateCause.OBSERVED)
            _make_stray_unconfirmed(wfs, "foo.txt")
            assert wfs.find(File, "foo.txt").get_state() == FileState.UNCONFIRMED

        builder = _make_builder(wfs)
        reporter = _FakeReporter()

        await rescan_files(wfs, reporter, builder)

        assert reporter.calls == [("STARTUP", "Checking 1 file(s) for changes")]
        async with wfs.db:
            assert wfs.find(File, "foo.txt").get_state() == FileState.CONFIRMED


async def test_rescan_files_confirms_deleted_stray_unconfirmed_row(wfs: Workflow, tmpdir):
    """A stray UNCONFIRMED row whose file is now absent must become MISSING, reported
    as DELETED, the same reporting as a regular CONFIRMED file being externally deleted."""
    with contextlib.chdir(tmpdir):
        with open("foo.txt", "w") as fh:
            fh.write("hello")
        async with wfs.db:
            declare_static(wfs, wfs.root, ["foo.txt"])
            _make_stray_unconfirmed(wfs, "foo.txt")
            assert wfs.find(File, "foo.txt").get_state() == FileState.UNCONFIRMED
        os.remove("foo.txt")

        builder = _make_builder(wfs)
        reporter = _FakeReporter()

        await rescan_files(wfs, reporter, builder)

        assert reporter.calls == [
            ("STARTUP", "Checking 1 file(s) for changes"),
            ("DELETED", "foo.txt"),
        ]
        async with wfs.db:
            assert wfs.find(File, "foo.txt").get_state() == FileState.MISSING


async def test_rescan_files_reports_externally_updated_static_file(wfs: Workflow, tmpdir):
    """A regular (non-UNCONFIRMED) CONFIRMED file that changed on disk must still be picked
    up by the scan, reported as UPDATED, and get its new hash applied."""
    with contextlib.chdir(tmpdir):
        with open("foo.txt", "w") as fh:
            fh.write("hello")
        async with wfs.db:
            file = declare_static(wfs, wfs.root, ["foo.txt"])[0]
            old_hash = file.get_hash()
        with open("foo.txt", "w") as fh:
            fh.write("changed")

        builder = _make_builder(wfs)
        reporter = _FakeReporter()

        await rescan_files(wfs, reporter, builder)

        assert reporter.calls == [
            ("STARTUP", "Checking 1 file(s) for changes"),
            (
                "UPDATED",
                "foo.txt (digest ddab29ff ➜ d67e2e94, size 49 ➜ 7, mode ?rw-r--r-- ➜ -rw-r--r--)",
            ),
        ]
        async with wfs.db:
            assert wfs.find(File, "foo.txt").get_state() == FileState.CONFIRMED
            assert wfs.find(File, "foo.txt").get_hash() != old_hash


async def test_rescan_files_applies_whole_scan_in_one_transaction(
    wfs: Workflow, tmpdir, monkeypatch
):
    """The scan applies one hash per file, but opens a single transaction to do so.

    A transaction per file is what this batching exists to avoid,
    so the count is pinned rather than merely the resulting states.
    """
    with contextlib.chdir(tmpdir):
        paths = ["a.txt", "b.txt", "c.txt"]
        for path in paths:
            with open(path, "w") as fh:
                fh.write(path)
        async with wfs.db:
            declare_static(wfs, wfs.root, paths)
        for path in paths:
            with open(path, "w") as fh:
                fh.write("changed " + path)

        builder = _make_builder(wfs)
        reporter = _FakeReporter()
        applied = []
        orig = Workflow.update_file_hash

        def spy(self, path, new_fh, *, cause):
            applied.append((path, cause, self.db._transaction_i))
            return orig(self, path, new_fh, cause=cause)

        # Workflow is a slotted attrs class, so the spy goes on the class, not the instance.
        monkeypatch.setattr(Workflow, "update_file_hash", spy)

        await rescan_files(wfs, reporter, builder)

        assert [path for path, _, _ in applied] == paths
        assert {cause for _, cause, _ in applied} == {HashUpdateCause.OBSERVED}
        assert len({transaction_i for _, _, transaction_i in applied}) == 1
        async with wfs.db:
            for path in paths:
                assert wfs.find(File, path).get_state() == FileState.CONFIRMED


async def test_rescan_nglobs_persists_readable_matches(wfp: Workflow, tmpdir):
    """A restart-detected nglob change (files added/removed while the director was not
    running) must persist matches in the same format later reads expect.

    Every read path (`Workflow.nglobs`, `Step.nglobs`, `browse.py`) expects the
    `nglob.data` column to hold JSON, via `json_converter` (see `stepup.core.cattrs`), so
    `rescan_nglobs` must persist with the same encoding rather than `pickle.dumps`.
    A mismatch would be invisible in the integration examples, because the owning step
    (typically the perpetually-rerunning `PLAN` step) usually re-registers its nglob with
    correct JSON before anything reads the row again -- but a read in that window (e.g. a
    concurrent `stepup graph`, or a step that doesn't rerun immediately) would hit a
    `json.JSONDecodeError` on the pickled bytes.
    """
    with contextlib.chdir(tmpdir):
        with open("inp1.txt", "w"):
            pass
        with open("inp2.txt", "w"):
            pass
        async with wfp.db:
            plan = wfp.find(Step, "./plan.py")
            ng = NamedGlob("inp*.txt")
            ng.extend(["inp1.txt", "inp2.txt"])
            wfp.register_nglob(plan, ng)
            plan.mark_completed(StepHash(b"ok", None, b"inp_ok", None), False)
            assert plan.get_state() == StepState.SUCCEEDED

        # Simulate files changing while the director was not running:
        # inp2.txt is deleted and inp3.txt appears.
        os.remove("inp2.txt")
        with open("inp3.txt", "w"):
            pass

        reporter = _FakeReporter()
        await rescan_nglobs(wfp, reporter)

        assert reporter.calls == [
            ("STARTUP", "Checking 1 nglob(s) for new or deleted matches"),
            ("DELETED", "inp2.txt"),
            ("UPDATED", "inp3.txt"),
        ]
        async with wfp.db:
            assert plan.get_state() == StepState.PENDING
            assert plan.get_hash() is None
            # The critical check: reading the persisted matches back must not raise,
            # and must reflect the fresh scan, not the pickled (or stale) old one.
            row = wfp.db.execute("SELECT data FROM nglob WHERE node = ?", (plan.i,)).fetchone()
            assert isinstance(row[0], str)
            new_nglobs = [reg_ng for _i, reg_ng, _step in wfp.nglob_registrations()]
            assert len(new_nglobs) == 1
            assert new_nglobs[0].files() == ["inp1.txt", "inp3.txt"]


async def test_watch_known_dirs_includes_glob_base_dirs(wfp: Workflow, tmpdir):
    """A directory that only ever appears as a glob pattern's base directory (no
    static() declaration, no recorded match) must still be watched after a restart, or
    a directory that only ever contained glob matches would go unwatched.
    """
    with contextlib.chdir(tmpdir):
        os.makedirs("data")
        async with wfp.db:
            plan = wfp.find(Step, "./plan.py")
            wfp.register_nglob(plan, NamedGlob("data/*.txt"))

        # Drain what fixture setup and register_nglob already queued, so only
        # watch_known_dirs's own contribution is observed below.
        while not wfp.dir_queue.empty():
            wfp.dir_queue.get_nowait()

        reporter = _FakeReporter()
        await watch_known_dirs(wfp, reporter)

        watched = set()
        while not wfp.dir_queue.empty():
            watched.add(wfp.dir_queue.get_nowait())
        assert "data" in watched
