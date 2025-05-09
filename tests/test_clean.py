# StepUp Core provides the basic framework for the StepUp build tool.
# © 2024–2025 Toon Verstraelen
#
# This file is part of StepUp Core.
#
# StepUp Core is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 3
# of the License, or (at your option) any later version.
#
# StepUp Core is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, see <http://www.gnu.org/licenses/>
#
# --
"""Unit tests for stepup.core.cleanup."""

from conftest import declare_static, fake_hash

from stepup.core.clean import search_consuming_paths
from stepup.core.enums import FileState
from stepup.core.file import File
from stepup.core.hash import FileHash
from stepup.core.step import Step
from stepup.core.workflow import Workflow


def test_cleanup_simple1(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    wfp.define_step(plan, "cp bar.txt foo.txt", inp_paths=["bar.txt"], out_paths=["foo.txt"])
    assert search_consuming_paths(wfp.con, ["bar.txt"]) == []


def test_cleanup_simple2(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    declare_static(wfp, plan, ["bar.txt"])
    wfp.define_step(plan, "cp bar.txt foo.txt", inp_paths=["bar.txt"], out_paths=["foo.txt"])
    file_hash = fake_hash("foo.txt")
    wfp.update_file_hashes([("foo.txt", file_hash)], "succeeded")
    assert search_consuming_paths(wfp.con, ["bar.txt"]) == [("foo.txt", FileState.BUILT, file_hash)]


def test_cleanup_simple3(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    declare_static(wfp, plan, ["bar.txt"])
    wfp.define_step(plan, "cp bar.txt foo.txt", inp_paths=["bar.txt"], out_paths=["foo.txt"])
    file_hash = fake_hash("foo.txt")
    wfp.update_file_hashes([("foo.txt", file_hash)], "succeeded")
    wfp.find(File, "foo.txt").set_state(FileState.OUTDATED)
    assert search_consuming_paths(wfp.con, ["bar.txt"]) == [
        ("foo.txt", FileState.OUTDATED, file_hash),
    ]


def test_cleanup_simple4(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    wfp.define_step(plan, "prog1", inp_paths=["bar.txt"], out_paths=["foo.txt"])
    file_hash = fake_hash("foo.txt")
    wfp.update_file_hashes([("foo.txt", file_hash)], "succeeded")
    wfp.define_step(plan, "prog2", inp_paths=["foo.txt"], vol_paths=["egg.txt"])
    assert search_consuming_paths(wfp.con, ["bar.txt"]) == [
        ("foo.txt", FileState.BUILT, file_hash),
        ("egg.txt", FileState.VOLATILE, FileHash.unknown()),
    ]


def test_cleanup_volatile(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    wfp.define_step(plan, "prog1", inp_paths=["inp.txt"], vol_paths=["vol.txt"])
    # Volatile files are always removed, without checking hashes
    assert search_consuming_paths(wfp.con, ["inp.txt"]) == [
        ("vol.txt", FileState.VOLATILE, FileHash.unknown()),
    ]
