# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Unit tests for stepup.core.director.finalize."""

import contextlib

from core_common import fake_hash

from stepup.core.director.finalize import remove_deletable_files
from stepup.core.director.reporter import ReporterClient
from stepup.core.graph.workflow import Workflow
from stepup.core.path import Path


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


async def test_remove_deletable_files_prunes_parents(tmpdir):
    """The directories of a removed output are pruned upward, as long as they are empty.

    An empty directory that was never queued, and the parent that holds it, are left alone.
    """
    with contextlib.chdir(tmpdir):
        Path("a/b").makedirs_p()
        Path("a/b/out.txt").write_text("x", encoding="utf-8")
        Path("c/d").makedirs_p()
        Path("c/d/out.txt").write_text("x", encoding="utf-8")
        Path("c/user").mkdir()
        to_be_deleted = {"a/b/out.txt": None, "a/b/": None, "c/d/out.txt": None, "c/d/": None}
        client = RecordingClient()
        await remove_deletable_files(to_be_deleted, ReporterClient(client))
        assert not Path("a").exists()
        assert not Path("c/d").exists()
        assert Path("c/user").is_dir()
        removed = {str(description) for tag, description in client.reports if tag == "REMOVE"}
        assert removed == {"a/b/out.txt", "c/d/out.txt", "a/b", "a", "c/d"}
