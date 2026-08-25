# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Unit tests for stepup.core.finalize."""

import contextlib

from conftest import fake_hash
from path import Path

from stepup.core.finalize import remove_deletable_files
from stepup.core.reporter import ReporterClient
from stepup.core.workflow import Workflow


class RecordingClient:
    """A minimal RPC client that collects the reports sent to it."""

    def __init__(self):
        self.reports = []

    @property
    def call(self):
        return self

    async def report(self, tag: str, description: str, pages: list[tuple[str, str]]):
        self.reports.append((tag, description))


async def test_remove_deletable_files_unhashable(wfp: Workflow, tmpdir):
    """A queued file that turned into a directory is skipped instead of crashing the cleanup.

    `FileHash.refreshed` raises `HashFailedError`, which is not an `OSError`,
    so it would otherwise propagate all the way out of `Builder.finalize`.
    """
    with contextlib.chdir(tmpdir):
        Path("out.txt").mkdir()
        to_be_deleted = {"out.txt": fake_hash("out.txt")}
        client = RecordingClient()
        await remove_deletable_files(to_be_deleted, ReporterClient(client))
        assert Path("out.txt").is_dir()
        assert ("WARNING", "Not removing out.txt: it cannot be hashed.") in client.reports
