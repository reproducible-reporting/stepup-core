# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Unit tests for stepup.core.cleanup."""

from conftest import declare_static, fake_hash

from stepup.core.clean import search_consuming_paths
from stepup.core.enums import FileState, HashUpdateCause
from stepup.core.file import File
from stepup.core.hash import FileHash
from stepup.core.step import Step
from stepup.core.workflow import Workflow


async def test_cleanup_simple1(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "cp bar.txt foo.txt", inp_paths=["bar.txt"], out_paths=["foo.txt"])
        assert search_consuming_paths(wfp.db, ["bar.txt"], False) == []


async def test_cleanup_simple2(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        declare_static(wfp, plan, ["bar.txt"])
        wfp.define_step(plan, "cp bar.txt foo.txt", inp_paths=["bar.txt"], out_paths=["foo.txt"])
        file_hash = fake_hash("foo.txt")
        wfp.update_file_hash("foo.txt", file_hash, cause=HashUpdateCause.SUCCEEDED)
        assert search_consuming_paths(wfp.db, ["bar.txt"], False) == [
            ("foo.txt", FileState.BUILT, False, file_hash)
        ]


async def test_cleanup_simple3(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        declare_static(wfp, plan, ["bar.txt"])
        wfp.define_step(plan, "cp bar.txt foo.txt", inp_paths=["bar.txt"], out_paths=["foo.txt"])
        file_hash = fake_hash("foo.txt")
        wfp.update_file_hash("foo.txt", file_hash, cause=HashUpdateCause.SUCCEEDED)
        wfp.find(File, "foo.txt").set_state(FileState.OUTDATED)
        assert search_consuming_paths(wfp.db, ["bar.txt"], False) == [
            ("foo.txt", FileState.OUTDATED, False, file_hash),
        ]


async def test_cleanup_simple4(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "prog1", inp_paths=["bar.txt"], out_paths=["foo.txt"])
        file_hash = fake_hash("foo.txt")
        wfp.update_file_hash("foo.txt", file_hash, cause=HashUpdateCause.SUCCEEDED)
        wfp.define_step(plan, "prog2", inp_paths=["foo.txt"], vol_paths=["egg.txt"])
        assert search_consuming_paths(wfp.db, ["bar.txt"], False) == [
            ("foo.txt", FileState.BUILT, False, file_hash),
            ("egg.txt", FileState.VOLATILE, False, FileHash.unknown()),
        ]


async def test_cleanup_volatile(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "prog1", inp_paths=["inp.txt"], vol_paths=["vol.txt"])
        # Volatile files are always removed, without checking hashes
        assert search_consuming_paths(wfp.db, ["inp.txt"], False) == [
            ("vol.txt", FileState.VOLATILE, False, FileHash.unknown()),
        ]


async def test_cleanup_detached(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "prog1", inp_paths=["inp.txt"], out_paths=["out.txt"])
        file_hash = fake_hash("out.txt")
        wfp.update_file_hash("out.txt", file_hash, cause=HashUpdateCause.SUCCEEDED)
        # Mark step (and indirectly its output file) as detached
        wfp.find(Step, "prog1").detach()
        assert search_consuming_paths(wfp.db, ["inp.txt"], False) == [
            ("out.txt", FileState.BUILT, True, file_hash),
        ]
