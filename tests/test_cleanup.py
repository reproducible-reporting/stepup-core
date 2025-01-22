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

from conftest import declare_static

from stepup.core.cleanup import search_consuming_paths
from stepup.core.enums import FileState
from stepup.core.hash import FileHash


def test_cleanup_simple1(wfp):
    plan = wfp.find("step", "./plan.py")
    wfp.define_step(plan, "cp bar.txt foo.txt", inp_paths=["bar.txt"], out_paths=["foo.txt"])
    assert search_consuming_paths(wfp.con, ["bar.txt"]) == []


def test_cleanup_simple2(wfp):
    plan = wfp.find("step", "./plan.py")
    declare_static(wfp, plan, ["bar.txt"])
    wfp.define_step(plan, "cp bar.txt foo.txt", inp_paths=["bar.txt"], out_paths=["foo.txt"])
    wfp.find("file", "foo.txt").set_state(FileState.BUILT)
    assert search_consuming_paths(wfp.con, ["bar.txt"]) == [("foo.txt", FileHash.unknown())]


def test_cleanup_simple3(wfp):
    plan = wfp.find("step", "./plan.py")
    declare_static(wfp, plan, ["bar.txt"])
    wfp.define_step(plan, "cp bar.txt foo.txt", inp_paths=["bar.txt"], out_paths=["foo.txt"])
    wfp.find("file", "foo.txt").set_state(FileState.OUTDATED)
    assert search_consuming_paths(wfp.con, ["bar.txt"]) == [("foo.txt", FileHash.unknown())]


def test_cleanup_simple4(wfp):
    plan = wfp.find("step", "./plan.py")
    wfp.define_step(plan, "prog1", inp_paths=["bar.txt"], out_paths=["foo.txt"])
    wfp.find("file", "foo.txt").set_state(FileState.BUILT)
    wfp.define_step(plan, "prog2", inp_paths=["foo.txt"], vol_paths=["egg.txt"])
    assert search_consuming_paths(wfp.con, ["bar.txt"]) == [
        ("foo.txt", FileHash.unknown()),
        ("egg.txt", FileHash.unknown()),
    ]
