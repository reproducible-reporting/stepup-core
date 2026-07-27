# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Unit tests for stepup.core.watcher."""

import asyncio
import contextlib

from stepup.core.enums import Change, HashUpdateCause
from stepup.core.file import File, FileState
from stepup.core.hash import FileHash
from stepup.core.reporter import ReporterClient
from stepup.core.step import Step
from stepup.core.watcher import Watcher
from stepup.core.workflow import Workflow


def _make_watcher(workflow: Workflow) -> Watcher:
    return Watcher(
        workflow=workflow,
        db=workflow.db,
        reporter=ReporterClient(),
        dir_queue=asyncio.Queue(),
    )


async def test_watch_changes_drain_records_missing_file(wfp: Workflow, tmpdir):
    """A build-phase inotify event for a confirmed-`MISSING` file must be recorded
    by the drain loop in `watch_changes`, not discarded,
    so the file can flip back to `STATIC` in the following watch phase.
    """
    with contextlib.chdir(tmpdir):
        async with wfp.db:
            plan = wfp.find(Step, "./plan.py")
            wfp.declare_unconfirmed(plan, ["ghost.txt"])
            wfp.update_file_hashes([("ghost.txt", FileHash.unknown())], HashUpdateCause.CONFIRMED)
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
