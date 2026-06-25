# StepUp Core provides the basic framework for the StepUp build tool.
# Copyright 2024-2026 Toon Verstraelen
#
# This file is part of StepUp Core.
#
# StepUp Core is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 3
# of the License, or (at your option) any later version.
#
# StepUp Core is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the REQUIRED warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, see <http://www.gnu.org/licenses/>
#
# --
"""Unit tests for stepup.core.workflow."""

import hashlib
import sqlite3
from collections import Counter

import pytest
from conftest import declare_static, fake_hash
from path import Path

from stepup.core.enums import FileState, HashUpdateCause, StepState
from stepup.core.exceptions import GraphError
from stepup.core.file import File
from stepup.core.hash import FileHash, StepHash
from stepup.core.nglob import NGlobMulti
from stepup.core.static_tree import StaticTree
from stepup.core.step import Step
from stepup.core.workflow import RECURSE_DEFERRED_INPUTS, RECURSE_OUTDATED_STEPS, Workflow

TEST_FILE_GRAPH = """\
root:
             creates   file:script.sh

file:script.sh
               state = STATIC
              digest = 116b4e2b ac3e35be fdfae3f3 b6eb8891 1689c30b fe6696d9 e6b0139d a8cb5e72
          created by   root:
"""


def test_file(wfs: Workflow):
    declare_static(wfs, wfs.root, ["script.sh"])
    assert wfs.format_str() == TEST_FILE_GRAPH
    file3 = wfs.find(File, "script.sh")
    assert isinstance(file3, File)
    assert file3.path == "script.sh"
    assert file3.key() == "file:script.sh"
    assert file3.get_state() == FileState.STATIC
    assert set(wfs.nodes(File)) == {file3}
    # We can declare static files without making their parents static.
    declare_static(wfs, wfs.root, ["unknown/foo.txt"])


def test_invalid_path(wfs):
    with pytest.raises(ValueError):
        declare_static(wfs, wfs.root, [""])
    with pytest.raises(ValueError):
        declare_static(wfs, wfs.root, ["."])
    with pytest.raises(ValueError):
        declare_static(wfs, wfs.root, ["foo/."])
    with pytest.raises(ValueError):
        declare_static(wfs, wfs.root, ["foo/bar/.."])


TEST_STEP_GRAPH = """\
root:
             creates   step:cp foo.txt sub/bar.txt

step:cp foo.txt sub/bar.txt
               state = PENDING
                need = DEFAULT
          created by   root:
            consumes   (file:foo.txt)
             creates   file:sub/bar.txt
            supplies   file:sub/bar.txt

(file:foo.txt)
               state = AWAITED
            supplies   step:cp foo.txt sub/bar.txt

file:sub/bar.txt
               state = AWAITED
          created by   step:cp foo.txt sub/bar.txt
            consumes   step:cp foo.txt sub/bar.txt
"""


TEST_STEP_GRAPH2 = """\
root:
             creates   step:cp foo.txt sub/bar.txt

step:cp foo.txt sub/bar.txt
               state = RUNNING
                need = DEFAULT
          created by   root:
            consumes   (file:foo.txt)
            consumes   (file:spam.txt) [amended]
             creates   file:egg.csv
             creates   file:sub/bar.txt
            supplies   file:egg.csv [amended]
            supplies   file:sub/bar.txt

(file:foo.txt)
               state = AWAITED
            supplies   step:cp foo.txt sub/bar.txt

file:sub/bar.txt
               state = AWAITED
          created by   step:cp foo.txt sub/bar.txt
            consumes   step:cp foo.txt sub/bar.txt

(file:spam.txt)
               state = AWAITED
            supplies   step:cp foo.txt sub/bar.txt

file:egg.csv
               state = AWAITED
          created by   step:cp foo.txt sub/bar.txt
            consumes   step:cp foo.txt sub/bar.txt
"""


def test_step(wfs: Workflow):
    # Normal case
    with wfs.con:
        to_check = wfs.define_step(
            wfs.root, "cp foo.txt sub/bar.txt", inp_paths=["foo.txt"], out_paths=["sub/bar.txt"]
        )
        assert to_check == []
        step = wfs.find(Step, "cp foo.txt sub/bar.txt")
        assert step.key() == "step:cp foo.txt sub/bar.txt"
        command, workdir = step.get_command_workdir()
        assert command == "cp foo.txt sub/bar.txt"
        assert workdir == Path(".")
        assert isinstance(workdir, Path)
        assert wfs.format_str() == TEST_STEP_GRAPH
        assert list(wfs.nodes(Step)) == [step]
        assert set(step.inp_paths(yield_detached=True)) == {("foo.txt", True)}
        assert set(step.out_paths()) == {"sub/bar.txt"}
        assert set(wfs.detached_inp_paths()) == {("foo.txt", FileState.AWAITED)}

    # Redefining the boot script is not allowed.
    with pytest.raises(GraphError), wfs.con:
        wfs.define_step(
            wfs.root, "cp foo.txt sub/bar.txt", inp_paths=["foo.txt"], out_paths=["sub/bar.txt"]
        )
    assert wfs.format_str() == TEST_STEP_GRAPH

    # Make the step RUNNING and test amending stuff.
    # (The extra inputs and outputs are not meant to be sensible for the copy command.)
    with wfs.con:
        step.set_state(StepState.RUNNING)
        assert step.get_rescheduled_info() == ""
    with wfs.con:
        keep_going, to_check = wfs.amend_step(step, inp_paths=["spam.txt"], out_paths=["egg.csv"])
        assert to_check == []
        assert not keep_going
        assert step.get_rescheduled_info().splitlines() == ["spam.txt"]
        assert set(step.inp_paths(yield_detached=True)) == {
            ("foo.txt", True),
            ("spam.txt", True),
        }
        assert set(step.out_paths()) == {"egg.csv", "sub/bar.txt"}
        assert wfs.format_str() == TEST_STEP_GRAPH2

    # Amend an input that was already known, which just gets ignored.
    with wfs.con:
        wfs.amend_step(step, inp_paths=["foo.txt"])
        assert wfs.format_str() == TEST_STEP_GRAPH2

    # Try a few things that should raise errors
    with pytest.raises(GraphError), wfs.con:
        # Amend an output that was already known.
        wfs.amend_step(step, out_paths=["egg.csv"])
    assert wfs.format_str() == TEST_STEP_GRAPH2
    with pytest.raises(GraphError), wfs.con:
        # Amend a new input and an output that was already known.
        wfs.amend_step(step, inp_paths=["new.zip"], out_paths=["egg.csv"])
    assert wfs.format_str() == TEST_STEP_GRAPH2


TEST_SIMPLE_EXAMPLE_GRAPH1 = """\
root:
             creates   step:cp foo.txt bar.txt

step:cp foo.txt bar.txt
               state = PENDING
                need = DEFAULT
          created by   root:
            consumes   (file:foo.txt)
             creates   file:bar.txt
            supplies   file:bar.txt

(file:foo.txt)
               state = AWAITED
            supplies   step:cp foo.txt bar.txt

file:bar.txt
               state = AWAITED
          created by   step:cp foo.txt bar.txt
            consumes   step:cp foo.txt bar.txt
"""

TEST_SIMPLE_EXAMPLE_GRAPH2 = """\
root:
             creates   file:foo.txt
             creates   step:cp foo.txt bar.txt

step:cp foo.txt bar.txt
               state = PENDING
                need = DEFAULT
          created by   root:
            consumes   file:foo.txt
             creates   file:bar.txt
            supplies   file:bar.txt

file:foo.txt
               state = STATIC
              digest = ddab29ff 2c393ee5 2855d21a 240eb05f 775df88e 3ce347df 759f0c4b 80356c35
          created by   root:
            supplies   step:cp foo.txt bar.txt

file:bar.txt
               state = AWAITED
          created by   step:cp foo.txt bar.txt
            consumes   step:cp foo.txt bar.txt
"""

TEST_SIMPLE_EXAMPLE_GRAPH3 = """\
root:
             creates   file:foo.txt
             creates   step:cp foo.txt bar.txt

step:cp foo.txt bar.txt
               state = SUCCEEDED
                need = DEFAULT
          inp_digest = 7c9d6cf6 ff15a9db f94295ad 7661e93e 22c7ce39 2c25303e f8553235 87b8a198
          out_digest = 989a8ef2 4a8ea52e 844a0770 1bfae079 4a7088e1 6a2ba779 3dfacd9a f1164aa1
           explained = yes
          created by   root:
            consumes   file:foo.txt
             creates   file:bar.txt
            supplies   file:bar.txt

file:foo.txt
               state = STATIC
              digest = ddab29ff 2c393ee5 2855d21a 240eb05f 775df88e 3ce347df 759f0c4b 80356c35
          created by   root:
            supplies   step:cp foo.txt bar.txt

file:bar.txt
               state = BUILT
              digest = 08bd2d24 7cc7aa38 b8c4b7fd 20ee7eda d0b593c3 debce92f 595c9d01 6da40bae
          created by   step:cp foo.txt bar.txt
            consumes   step:cp foo.txt bar.txt
"""

TEST_SIMPLE_EXAMPLE_GRAPH4 = """\
root:
             creates   file:foo.txt
             creates   step:cp foo.txt bar.txt

step:cp foo.txt bar.txt
               state = PENDING
                need = DEFAULT
          inp_digest = 7c9d6cf6 ff15a9db f94295ad 7661e93e 22c7ce39 2c25303e f8553235 87b8a198
          out_digest = 989a8ef2 4a8ea52e 844a0770 1bfae079 4a7088e1 6a2ba779 3dfacd9a f1164aa1
           explained = yes
          created by   root:
            consumes   file:foo.txt
             creates   file:bar.txt
            supplies   file:bar.txt

file:foo.txt
               state = STATIC
              digest = ddab29ff 2c393ee5 2855d21a 240eb05f 775df88e 3ce347df 759f0c4b 80356c35
          created by   root:
            supplies   step:cp foo.txt bar.txt

file:bar.txt
               state = OUTDATED
              digest = 08bd2d24 7cc7aa38 b8c4b7fd 20ee7eda d0b593c3 debce92f 595c9d01 6da40bae
          created by   step:cp foo.txt bar.txt
            consumes   step:cp foo.txt bar.txt
"""


def test_simple_example(wfs: Workflow):
    # Create a runnable step and check it
    with wfs.con:
        to_check = wfs.define_step(
            wfs.root, "cp foo.txt bar.txt", inp_paths=["foo.txt"], out_paths=["bar.txt"]
        )
        assert to_check == []
        assert wfs.format_str() == TEST_SIMPLE_EXAMPLE_GRAPH1
        step = wfs.find(Step, "cp foo.txt bar.txt")

    # Declare the static input and check graph
    with wfs.con:
        foo = declare_static(wfs, wfs.root, ["foo.txt"])[0]
        assert wfs.format_str() == TEST_SIMPLE_EXAMPLE_GRAPH2
        assert wfs.get_file_counts() == {FileState.STATIC: 1, FileState.AWAITED: 1}
        assert wfs.get_step_counts() == {StepState.PENDING: 1}

    # Verify things that should not be allowed
    with pytest.raises(GraphError), wfs.con:
        declare_static(wfs, wfs.root, ["bar.txt"])

    # Simulate the builder, pretending to execute the step
    with wfs.con:
        out_hashes = [("bar.txt", fake_hash("bar.txt"))]
        wfs.update_file_hashes(out_hashes, HashUpdateCause.SUCCEEDED)
        inp_hashes = [("foo.txt", foo.get_hash())]
        env_vars_values = [("A", "B")]
        step_hash = StepHash.from_inp(step.key(), True, inp_hashes, env_vars_values)
        step_hash = step_hash.evolve_out(out_hashes)
        step.completed(step_hash)
        assert wfs.format_str() == TEST_SIMPLE_EXAMPLE_GRAPH3
        assert wfs.get_file_counts() == Counter({FileState.STATIC: 1, FileState.BUILT: 1})
        assert wfs.get_step_counts() == Counter({StepState.SUCCEEDED: 1})

    # Check hashes
    step_hash2 = step.get_hash()
    assert step_hash2.inp_info.inp_hashes == dict(inp_hashes)
    assert step_hash2.inp_info.env_var_values == dict(env_vars_values)
    assert step_hash2.out_info.out_hashes == dict(out_hashes)

    # Verify things that should not be allowed
    with pytest.raises(GraphError), wfs.con:
        declare_static(wfs, wfs.root, ["foo.txt"])
    with pytest.raises(GraphError), wfs.con:
        declare_static(wfs, wfs.root, ["bar.txt"])

    # simulate a change in the input file
    wfs.update_file_hashes([("foo.txt", fake_hash("foo.txt"))], cause=HashUpdateCause.EXTERNAL)
    assert wfs.format_str() == TEST_SIMPLE_EXAMPLE_GRAPH4
    assert step.get_state() == StepState.PENDING

    # simulate a skip
    step.completed(step_hash)
    assert wfs.format_str() == TEST_SIMPLE_EXAMPLE_GRAPH3
    assert step.get_state() == StepState.SUCCEEDED


def test_define_boot_input_static(wfs: Workflow):
    to_check = wfs.define_step(wfs.root, "echo", inp_paths=["foo.txt"])
    assert to_check == []
    echo = wfs.find(Step, "echo")
    declare_static(wfs, wfs.root, ["foo.txt"])
    foo = wfs.find(File, "foo.txt")
    assert echo.creator() is not None
    assert list(foo.consumers()) == [echo]
    assert list(foo.suppliers()) == []
    assert list(echo.consumers()) == []
    assert set(echo.suppliers()) == {foo}


def test_command_workdir_string(wfs: Workflow):
    with pytest.raises(ValueError):
        wfs.define_step(wfs.root, "echo  # wd=foo")


def test_define_boot_static_input(wfs: Workflow):
    (foo,) = declare_static(wfs, wfs.root, ["foo.txt"])
    to_check = wfs.define_step(wfs.root, "echo", inp_paths=["foo.txt"])
    assert to_check == []
    echo = wfs.find(Step, "echo")
    assert echo.creator().i is not None
    assert list(foo.consumers()) == [echo]
    assert list(foo.suppliers()) == []
    assert list(echo.consumers()) == []
    assert set(echo.suppliers()) == {foo}


def test_redefine_boot(wfs: Workflow):
    with wfs.con:
        to_check = wfs.define_step(wfs.root, "echo 1")
        assert to_check == []
        step = wfs.find(Step, "echo 1")
    with pytest.raises(GraphError), wfs.con:
        wfs.define_step(wfs.root, "echo 2")
    with wfs.con:
        step.detach()
        wfs.define_step(wfs.root, "echo 3")


def test_define_boot_input_detached(wfs: Workflow):
    wfs.define_step(wfs.root, "echo", inp_paths=["foo.txt"])
    foo = wfs.find(File, "foo.txt")
    assert isinstance(foo, File)
    foo, detached = wfs.find_detached(File, "foo.txt")
    assert detached
    assert foo.is_detached()


def test_redefine_step(wfp: Workflow):
    with wfp.con:
        plan = wfp.find(Step, "./plan.py")
        to_check = wfp.define_step(plan, "echo")
        assert to_check == []
        echo = wfp.find(Step, "echo")
        assert not echo.is_detached()
        assert echo.get_state() == StepState.PENDING
        assert list(wfp.nodes(Step)) == [plan, echo]
    with pytest.raises(GraphError), wfp.con:
        wfp.define_step(plan, "echo")
    with wfp.con:
        echo.detach()
        assert echo.is_detached()
        wfp.define_step(plan, "echo")
        assert not echo.is_detached()
        assert echo.get_state() == StepState.PENDING


def test_define_step_input_static(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    to_check = wfp.define_step(plan, "cat given", inp_paths=["given"])
    assert to_check == []
    cat = wfp.find(Step, "cat given")
    assert cat.get_state() == StepState.PENDING
    given = wfp.find(File, "given")
    assert given.get_state() == FileState.AWAITED
    declare_static(wfp, plan, ["given"])
    assert given.get_state() == FileState.STATIC


def test_define_step_static_input(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    declare_static(wfp, plan, ["given"])
    wfp.define_step(plan, "cat given", inp_paths=["given"])
    cat = wfp.find(Step, "cat given")
    assert cat.get_state() == StepState.PENDING
    given = wfp.find(File, "given")
    assert given.get_state() == FileState.STATIC


def test_define_step_volatile_input(wfp: Workflow):
    with wfp.con:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "touch given", vol_paths=["given"])
        touch = wfp.find(Step, "touch given")
        file, detached = wfp.find_detached(File, "given")
        assert not detached
        assert file.get_state() == FileState.VOLATILE
    with pytest.raises(GraphError), wfp.con:
        # Volatile files are not allowed as inputs
        wfp.define_step(plan, "cat given", inp_paths=["given"])
    with wfp.con:
        touch.completed(StepHash(b"mock", None, b"zzz", None))
        assert touch.get_state() == StepState.SUCCEEDED
        assert file.get_state() == FileState.VOLATILE
    with pytest.raises(GraphError), wfp.con:
        # Volatile files are not allowed as inputs
        wfp.define_step(plan, "cat given", inp_paths=["given"])


def test_define_step_input_volatile(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    wfp.define_step(plan, "cat given", inp_paths=["given"])
    cat = wfp.find(Step, "cat given")
    assert cat.get_state() == StepState.PENDING
    file, detached = wfp.find_detached(File, "given")
    assert detached
    assert file.get_state() == FileState.AWAITED
    with pytest.raises(GraphError), wfp.con:
        wfp.define_step(plan, "touch given", vol_paths=["given"])


def test_file_state_static_overlap(wfp: Workflow):
    with wfp.con:
        plan = wfp.find(Step, "./plan.py")
        declare_static(wfp, plan, ["given"])
    with pytest.raises(GraphError), wfp.con:
        wfp.define_step(plan, "touch given", out_paths=["given"])
    with pytest.raises(GraphError), wfp.con:
        wfp.define_step(plan, "touch given", vol_paths=["given"])
    with wfp.con:
        wfp.define_step(plan, "echo", inp_paths=["some"], out_paths=["other"])
        step = wfp.find(Step, "echo")
        step.set_state(StepState.RUNNING)
        keep_going, to_check = wfp.amend_step(
            step, inp_paths=["inp"], out_paths=["out"], vol_paths=["vol"]
        )
        assert not keep_going
        assert to_check == []
        # Amending an existing input is tolerated.
        wfp.amend_step(step, inp_paths=["some"])
    with pytest.raises(GraphError), wfp.con:
        wfp.amend_step(step, out_paths=["given"])
    with pytest.raises(GraphError), wfp.con:
        wfp.amend_step(step, vol_paths=["given"])


def test_file_state_output_overlap(wfp: Workflow):
    with wfp.con:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "touch given", out_paths=["given"])
    with pytest.raises(GraphError), wfp.con:
        declare_static(wfp, plan, ["given"])
    with pytest.raises(GraphError), wfp.con:
        wfp.define_step(plan, "touch given", vol_paths=["given"])
    with wfp.con:
        wfp.define_step(plan, "echo", inp_paths=["some"], out_paths=["other"])
        step = wfp.find(Step, "echo")
        step.set_state(StepState.RUNNING)
        keep_going, to_check = wfp.amend_step(
            step, inp_paths=["inp", "given"], out_paths=["out"], vol_paths=["vol"]
        )
        assert not keep_going
        assert to_check == []
    with pytest.raises(GraphError), wfp.con:
        wfp.amend_step(step, out_paths=["given"])
    with pytest.raises(GraphError), wfp.con:
        wfp.amend_step(step, vol_paths=["given"])


def test_file_state_volatile_overlap(wfp: Workflow):
    with wfp.con:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "touch given", vol_paths=["given"])
    with pytest.raises(GraphError), wfp.con:
        declare_static(wfp, plan, ["given"])
    with pytest.raises(GraphError), wfp.con:
        wfp.define_step(plan, "touch given", out_paths=["given"])
    with wfp.con:
        wfp.define_step(plan, "echo", inp_paths=["some"], out_paths=["other"])
        step = wfp.find(Step, "echo")
        step.set_state(StepState.RUNNING)
        keep_going, to_check = wfp.amend_step(
            step, inp_paths=["inp"], out_paths=["out"], vol_paths=["vol"]
        )
        assert not keep_going
        assert to_check == []
    with pytest.raises(GraphError), wfp.con:
        wfp.amend_step(step, inp_paths=["given"])
    with pytest.raises(GraphError), wfp.con:
        wfp.amend_step(step, out_paths=["given"])
    with pytest.raises(GraphError), wfp.con:
        wfp.amend_step(step, vol_paths=["given"])


PENDING_STEP_SKIP_GRAPH = """\
root:
             creates   file:plan.py
             creates   step:./plan.py

file:plan.py
               state = STATIC
              digest = 4e929dac d83345e7 26c42517 5f6089aa 9b9513af 07615728 a82225e3 1383ff4f
          created by   root:
            supplies   step:./plan.py

step:./plan.py
               state = PENDING
                need = PLAN
          created by   root:
            consumes   file:plan.py
             creates   file:inp
             creates   step:cat < inp > out

file:inp
               state = STATIC
              digest = 29a9e775 80ac85ad 896542d4 5ae52e21 8428bbe9 b0c752bc 2785ed22 a6eca01a
          created by   step:./plan.py
            supplies   step:cat < inp > out

step:cat < inp > out
               state = SUCCEEDED
                need = DEFAULT
          inp_digest = 61616161 61616161 61616161 61616161 61616161 61616161 61616161 61616161
          out_digest = 62626262 62626262 62626262 62626262 62626262 62626262 62626262 62626262
          created by   step:./plan.py
            consumes   file:inp
             creates   file:out
            supplies   file:out

file:out
               state = BUILT
              digest = 762069bc 07a6e1b5 df123a5a e7bd91c1 0daa0469 4fbaa17f ba0cd6a8 dcce8f22
          created by   step:cat < inp > out
            consumes   step:cat < inp > out
"""


def test_define_pending_step_skip(wfp: Workflow):
    # Define workflow
    plan = wfp.find(Step, "./plan.py")
    declare_static(wfp, plan, ["inp"])
    wfp.define_step(plan, "cat < inp > out", inp_paths=["inp"], out_paths=["out"])
    step = wfp.find(Step, "cat < inp > out")

    # Simulate run (first get the plan step and ignore it)
    wfp.update_file_hashes([("out", fake_hash("out"))], HashUpdateCause.SUCCEEDED)
    step.completed(StepHash(b"a" * 32, None, b"b" * 32, None))

    # Check run
    assert step.get_state() == StepState.SUCCEEDED
    step_hash = step.get_hash()
    assert step_hash.inp_digest == b"a" * 32
    assert step_hash.out_digest == b"b" * 32
    assert wfp.format_str() == PENDING_STEP_SKIP_GRAPH

    # Simulate input change
    wfp.update_file_hashes([("inp", fake_hash("inp"))], HashUpdateCause.EXTERNAL)
    assert step.get_state() == StepState.PENDING
    out = wfp.find(File, "out")
    assert out.get_state() == FileState.OUTDATED

    # Simulate rerun
    assert step.get_state() == StepState.PENDING
    step.completed(StepHash(b"a" * 32, None, b"b" * 32, None))
    assert wfp.format_str() == PENDING_STEP_SKIP_GRAPH
    assert step.get_state() == StepState.SUCCEEDED
    step.delete_hash()
    assert step.get_hash() is None


def test_define_pending_step_skip_extra(wfp: Workflow):
    # Prepare jobs for normal run
    plan = wfp.find(Step, "./plan.py")
    declare_static(wfp, plan, ["ainp", "ainp2"])
    wfp.define_step(plan, "foo > log", env_vars=["VAR"], out_paths=["log"])
    foo = wfp.find(Step, "foo > log")
    assert foo.get_state() == StepState.PENDING
    wfp.define_step(foo, "bar > spam", inp_paths=["log"], env_vars=["X"], vol_paths=["spam"])
    bar = wfp.find(Step, "bar > spam")
    assert bar.get_state() == StepState.PENDING
    plan.completed(StepHash(b"plan_ok", None, b"zzz", None))

    # Simulate run
    # foo
    keep_going, to_check = wfp.amend_step(
        foo, inp_paths=["ainp"], out_paths=["aout"], vol_paths=["avol"]
    )
    assert keep_going
    assert to_check == []
    wfp.update_file_hashes(
        [("log", fake_hash("log")), ("aout", fake_hash("aout"))], HashUpdateCause.SUCCEEDED
    )
    foo.completed(StepHash(b"foo_ok", None, b"zzz", None))
    assert foo.get_state() == StepState.SUCCEEDED
    assert bar.get_state() == StepState.PENDING
    # bar
    bar.mark_pending()  # Should not hurt
    assert bar.get_state() == StepState.PENDING
    wfp.amend_step(bar, inp_paths=["ainp2"], out_paths=["aout2"], vol_paths=["avol2"])
    assert wfp.find(File, "ainp2") in set(bar.suppliers())
    wfp.update_file_hashes([("aout2", fake_hash("aout2"))], HashUpdateCause.SUCCEEDED)
    bar.completed(StepHash(b"bar_ok", None, b"zzz", None))
    assert bar.get_state() == StepState.SUCCEEDED
    txt = wfp.format_str()

    # Make foo pending and check state
    foo.mark_pending()
    assert foo.get_hash() is not None
    assert foo.get_state() == StepState.PENDING
    assert not foo.is_detached()

    assert wfp.find(File, "log").get_state() == FileState.OUTDATED
    # bar should also become pending
    assert bar.get_hash() is not None
    assert bar.get_state() == StepState.PENDING
    spam = wfp.find(File, "spam")
    assert spam is not None
    assert spam.get_state() == FileState.VOLATILE

    # Simulate rerun
    foo.mark_pending()
    assert foo.get_state() == StepState.PENDING
    assert bar.get_state() == StepState.PENDING
    # This simulation assumes that no files have changed and we can just skip foo.
    foo.completed(StepHash(b"foo_ok", None, b"zzz", None))
    assert foo.get_state() == StepState.SUCCEEDED
    assert bar.get_state() == StepState.PENDING
    # This simulation assumes that no files have changed and we can just skip bar
    bar.completed(StepHash(b"bar_ok", None, b"zzz", None))
    assert bar.get_state() == StepState.SUCCEEDED
    assert wfp.format_str() == txt


def test_skip_step_amended_detached_input(wfp: Workflow):
    # Prepare jobs for normal run
    plan = wfp.find(Step, "./plan.py")
    (ainp,) = declare_static(wfp, plan, ["ainp"])
    wfp.define_step(plan, "foo > log", out_paths=["log"])
    foo = wfp.find(Step, "foo > log")
    assert foo.get_state() == StepState.PENDING
    assert list(foo.out_paths()) == ["log"]

    # Simulate run
    wfp.amend_step(foo, inp_paths=["ainp"], out_paths=["aout"], vol_paths=["avol"])
    wfp.update_file_hashes(
        [("log", fake_hash("log")), ("aout", fake_hash("aout"))], HashUpdateCause.SUCCEEDED
    )
    foo.completed(StepHash(b"foo_ok", None, b"zzz", None))
    assert foo.get_state() == StepState.SUCCEEDED

    # Detach ainp and check state
    assert set(foo.out_paths()) == {"aout", "log"}
    ainp.detach()
    assert set(foo.out_paths()) == {"aout", "log"}
    assert foo.get_hash() is not None
    assert foo.get_state() == StepState.SUCCEEDED
    assert wfp.find(File, "log").get_state() == FileState.BUILT

    # When ainp reappears, foo should be rerun because ainp may have changed.
    declare_static(wfp, plan, ["ainp"])
    assert foo.get_hash() is not None
    assert foo.get_state() == StepState.PENDING
    log = wfp.find(File, "log")
    assert log.get_state() == FileState.OUTDATED


def test_skip_ngm(wfp: Workflow):
    # Prepare jobs for normal run
    plan = wfp.find(Step, "./plan.py")
    wfp.define_step(plan, "foo")
    foo = wfp.find(Step, "foo")
    assert foo.get_state() == StepState.PENDING
    plan.completed(StepHash(b"plan_ok", None, b"ee", None))
    assert plan.get_state() == StepState.SUCCEEDED

    # Simulate run
    ngm = NGlobMulti.from_patterns(["${*prefix}_data.txt"], subs={"prefix": "n???"})
    wfp.register_nglob(foo, ngm)
    foo.completed(StepHash(b"foo_ok", None, b"zzz", None))
    assert foo.get_hash() is not None
    assert foo.get_state() == StepState.SUCCEEDED

    # Make foo pending and check state
    foo.mark_pending()
    assert foo.get_hash() is not None
    assert foo.get_state() == StepState.PENDING

    # Skip
    assert foo.get_state() == StepState.PENDING
    foo.completed(StepHash(b"foo_ok", None, b"zzz", None))
    assert foo.get_state() == StepState.SUCCEEDED
    nglob_multis = list(foo.nglob_multis())
    assert len(nglob_multis) == 1
    assert len(nglob_multis[0].nglob_singles) == 1
    assert nglob_multis[0].nglob_singles[0].pattern == "${*prefix}_data.txt"
    assert nglob_multis[0].nglob_singles[0].subs == {"prefix": "n???"}
    assert nglob_multis[0].used_names == ("prefix",)
    assert nglob_multis[0].subs == {"prefix": "n???"}


def test_hash_completed_success(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    wfp.define_step(plan, "cp foo bar", inp_paths=["foo"], out_paths=["bar"])
    step_hash = StepHash(b"p" * 32, None, b"p" * 32, None)
    plan.completed(step_hash)
    assert step_hash == plan.get_hash()


def test_amend_step(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    wfp.define_step(plan, "blub > log", vol_paths=["log"])
    step = wfp.find(Step, "blub > log")
    assert wfp.amend_step(step)
    keep_going, to_check = wfp.amend_step(
        step, inp_paths=["inp1", "inp2"], out_paths=["out3"], vol_paths=["vol4"]
    )
    assert not keep_going
    assert to_check == []
    assert set(step.inp_paths(yield_detached=True, amended=True)) == {
        ("inp1", True),
        ("inp2", True),
    }
    assert set(step.out_paths(amended=True)) == {"out3"}
    assert set(step.vol_paths(amended=True)) == {"vol4"}
    step.completed(None)
    step.set_state(StepState.PENDING)
    declare_static(wfp, plan, ["inp1"])
    step.set_state(StepState.PENDING)
    declare_static(wfp, plan, ["inp2"])
    assert [node.key() for node in step.products()] == ["file:log", "file:out3", "file:vol4"]


PENDING_STEP_SKIP_AMENDED_GRAPH = """\
root:
             creates   file:plan.py
             creates   step:./plan.py

file:plan.py
               state = STATIC
              digest = 4e929dac d83345e7 26c42517 5f6089aa 9b9513af 07615728 a82225e3 1383ff4f
          created by   root:
            supplies   step:./plan.py

step:./plan.py
               state = PENDING
                need = PLAN
          created by   root:
            consumes   file:plan.py
             creates   file:ainp
             creates   file:inp
             creates   step:cat < inp > out 2> vol

file:ainp
               state = STATIC
              digest = c0a3760b 3f6ad19a 940952bc 5e60a7e3 e6554d97 f19114b7 765e21e0 a14cf4d6
          created by   step:./plan.py
            supplies   step:cat < inp > out 2> vol

file:inp
               state = STATIC
              digest = 29a9e775 80ac85ad 896542d4 5ae52e21 8428bbe9 b0c752bc 2785ed22 a6eca01a
          created by   step:./plan.py
            supplies   step:cat < inp > out 2> vol

step:cat < inp > out 2> vol
               state = SUCCEEDED
                need = DEFAULT
          inp_digest = 63636363 63636363 63636363 63636363 63636363 63636363 63636363 63636363
          out_digest = 64646464 64646464 64646464 64646464 64646464 64646464 64646464 64646464
          created by   step:./plan.py
            consumes   file:ainp [amended]
            consumes   file:inp
             creates   file:aout
             creates   file:avol
             creates   file:out
             creates   file:vol
            supplies   file:aout [amended]
            supplies   file:avol [amended]
            supplies   file:out
            supplies   file:vol

file:out
               state = BUILT
              digest = 762069bc 07a6e1b5 df123a5a e7bd91c1 0daa0469 4fbaa17f ba0cd6a8 dcce8f22
          created by   step:cat < inp > out 2> vol
            consumes   step:cat < inp > out 2> vol

file:vol
               state = VOLATILE
          created by   step:cat < inp > out 2> vol
            consumes   step:cat < inp > out 2> vol

file:aout
               state = BUILT
              digest = bff8fd60 206e04a5 f6052fe5 5896f8da b0fb3f74 fd92802e d68adedb 7b082496
          created by   step:cat < inp > out 2> vol
            consumes   step:cat < inp > out 2> vol

file:avol
               state = VOLATILE
          created by   step:cat < inp > out 2> vol
            consumes   step:cat < inp > out 2> vol
"""


def test_define_pending_step_skip_amended(wfp: Workflow):
    # Define workflow
    plan = wfp.find(Step, "./plan.py")
    declare_static(wfp, plan, ["inp", "ainp"])
    wfp.define_step(
        plan, "cat < inp > out 2> vol", inp_paths=["inp"], out_paths=["out"], vol_paths=["vol"]
    )
    step = wfp.find(Step, "cat < inp > out 2> vol")

    # Simulate running the step
    wfp.amend_step(step, inp_paths=["ainp"], out_paths=["aout"], vol_paths=["avol"])
    wfp.update_file_hashes(
        [("out", fake_hash("out")), ("aout", fake_hash("aout"))], HashUpdateCause.SUCCEEDED
    )
    step.completed(StepHash(b"c" * 32, None, b"d" * 32, None))
    assert wfp.format_str() == PENDING_STEP_SKIP_AMENDED_GRAPH
    assert step.get_state() == StepState.SUCCEEDED

    # Check run
    assert step.get_state() == StepState.SUCCEEDED
    step_hash = step.get_hash()
    assert step_hash.inp_digest == b"c" * 32
    assert step_hash.out_digest == b"d" * 32

    # Simulate amended input change
    wfp.update_file_hashes([("ainp", fake_hash("ainp"))], HashUpdateCause.EXTERNAL)
    assert step.get_state() == StepState.PENDING
    out = wfp.find(File, "out")
    assert out.get_state() == FileState.OUTDATED

    # Simulate and check rerun
    assert step.get_state() == StepState.PENDING
    step.completed(StepHash(b"c" * 32, None, b"d" * 32, None))
    assert {node.key() for node in step.suppliers(include_detached=True)} == {
        "file:ainp",
        "file:inp",
    }
    assert isinstance(wfp.find(File, "aout"), File)
    assert isinstance(wfp.find(File, "avol"), File)
    assert {node.key() for node in step.suppliers(include_detached=True)} == {
        "file:ainp",
        "file:inp",
    }
    assert wfp.find(File, "aout").creator() == step
    assert wfp.find(File, "avol").creator() == step
    assert wfp.format_str() == PENDING_STEP_SKIP_AMENDED_GRAPH
    assert step.get_state() == StepState.SUCCEEDED

    # Check deleteion of hash
    step.delete_hash()
    assert step.get_hash() is None


REGISTER_NGLOB_GRAPH = """\
root:
             creates   file:plan.py
             creates   step:./plan.py

file:plan.py
               state = STATIC
              digest = 4e929dac d83345e7 26c42517 5f6089aa 9b9513af 07615728 a82225e3 1383ff4f
          created by   root:
            supplies   step:./plan.py

step:./plan.py
               state = PENDING
                need = PLAN
          created by   root:
            consumes   file:plan.py
             creates   step:touch log

step:touch log
               state = PENDING
                need = DEFAULT
                 ngm = ['*.txt'] {}
          created by   step:./plan.py
             creates   file:log
            supplies   file:log

file:log
               state = VOLATILE
          created by   step:touch log
            consumes   step:touch log
"""


def test_register_nglob(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    wfp.define_step(plan, "touch log", vol_paths=["log"])
    step = wfp.find(Step, "touch log")
    ngm = NGlobMulti.from_patterns(["*.txt"])
    wfp.register_nglob(step, ngm)
    assert list(step.nglob_multis()) == [ngm]
    assert list(wfp.nglob_multis(yield_step=True)) == [(1, ngm, step)]
    assert wfp.format_str() == REGISTER_NGLOB_GRAPH

    # Detaching does not clear products
    step.detach()
    assert list(step.nglob_multis()) == [ngm]
    assert list(wfp.nglob_multis(yield_step=True)) == [(1, ngm, step)]
    wfp.clean()
    assert list(step.nglob_multis()) == []
    assert list(wfp.nglob_multis(yield_step=True)) == []


def test_is_relevant(wfp: Workflow):
    assert wfp.is_relevant("plan.py")
    assert not wfp.is_relevant("unknown.txt")
    plan = wfp.find(Step, "./plan.py")
    wfp.register_nglob(plan, NGlobMulti.from_patterns(["*.txt"]))
    assert wfp.is_relevant("unknown.txt")


def test_externally_updated1(wfp: Workflow):
    # Simulate creating and running two steps: one succeeds and one fails.
    with wfp.con:
        plan = wfp.find(Step, "./plan.py")
        declare_static(wfp, plan, ["aa1_foo.txt", "bb7_foo.txt", "cc5_foo.txt"])
        _ngm = NGlobMulti.from_patterns(
            ["${*prefix}_foo.txt", "${*prefix}_bar.txt"],
            {"prefix": "??[0-9]", "unused": "aa??"},
        )
        _ngm.extend(["aa1_foo.txt", "aa1_bar.txt", "bb7_foo.txt", "cc5_foo.txt"])
        wfp.register_nglob(plan, _ngm)
        wfp.define_step(
            plan, "work", inp_paths=["aa1_foo.txt"], out_paths=["aa1_bar.txt"], vol_paths=["log"]
        )
        work = wfp.find(Step, "work")
        plan.completed(StepHash(b"ok", None, b"inp_ok", None))
        aa1_bar = wfp.find(File, "aa1_bar.txt")
        assert aa1_bar.creator() == work
        assert aa1_bar.get_state() == FileState.AWAITED
        assert work.get_state() == StepState.PENDING
        wfp.update_file_hashes([("aa1_bar.txt", fake_hash("ok"))], HashUpdateCause.SUCCEEDED)
        work.completed(None)
        assert work.get_state() == StepState.FAILED
        assert aa1_bar.get_state() == FileState.OUTDATED
        assert list(wfp.steps(StepState.SUCCEEDED)) == [plan]
        assert list(wfp.steps(StepState.FAILED)) == [work]
        cc5_foo = wfp.find(File, "cc5_foo.txt")
        assert cc5_foo is not None
        assert cc5_foo.get_state() == FileState.STATIC
        print(cc5_foo.i)

    # Simulate external changes.
    with wfp.con:
        # Changes:
        # - Delete `cc5_foo.txt` (static but not used)
        # - Update `aa1_bar.txt` (output of work, must be repeated)
        # - Update `bb7_bar.txt` (not used, trigggers a change in the nglob results)
        wfp.update_file_hashes(
            [
                ("cc5_foo.txt", FileHash.unknown()),
                ("aa1_bar.txt", fake_hash("change1")),
            ],
            HashUpdateCause.EXTERNAL,
        )
        wfp.process_nglob_changes({"cc5_foo.txt"}, {"bb7_bar.txt"})

    # The top-level plan became pending (and pending again), so the step work becomes detached.
    assert work.get_state() == StepState.PENDING
    assert not work.is_detached()
    assert not aa1_bar.is_detached()
    assert aa1_bar.get_state() == FileState.AWAITED
    assert cc5_foo is not None
    assert cc5_foo.get_state() == FileState.MISSING
    assert wfp.find(File, "bb7_bar.txt") is None
    ngm = next(plan.nglob_multis())
    assert ngm.files() == ("aa1_bar.txt", "aa1_foo.txt", "bb7_bar.txt", "bb7_foo.txt")
    assert ngm.nglob_singles[0].results == {("aa1",): {"aa1_foo.txt"}, ("bb7",): {"bb7_foo.txt"}}
    assert ngm.nglob_singles[1].results == {("aa1",): {"aa1_bar.txt"}, ("bb7",): {"bb7_bar.txt"}}


def test_externally_updated_static_detached(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    declare_static(wfp, plan, ["foo.txt"])
    foo = wfp.find(File, "foo.txt")
    foo.detach()
    foo.set_state(FileState.MISSING)
    wfp.update_file_hashes([("foo.txt", fake_hash("foo.txt"))], HashUpdateCause.EXTERNAL)
    assert foo.is_detached()
    assert foo.get_state() == FileState.STATIC


def test_externally_updated_static_missing(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    declare_static(wfp, plan, ["foo.txt"])
    foo = wfp.find(File, "foo.txt")
    foo.set_state(FileState.MISSING)
    wfp.update_file_hashes([("foo.txt", fake_hash("foo.txt"))], HashUpdateCause.EXTERNAL)
    assert foo.creator().i == plan.i
    assert foo.get_state() == FileState.STATIC


def test_externally_deleted_static_detached(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    declare_static(wfp, plan, ["foo.txt"])
    foo = wfp.find(File, "foo.txt")
    foo.detach()
    wfp.update_file_hashes([("foo.txt", FileHash.unknown())], HashUpdateCause.EXTERNAL)
    assert foo.is_detached()
    assert foo.get_state() == FileState.MISSING
    assert foo.get_hash() == FileHash.unknown()


def test_externally_updated_built_detached(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    wfp.define_step(plan, "touch foo.txt", out_paths=["foo.txt"])
    step = wfp.find(Step, "touch foo.txt")
    step.detach()
    assert step.get_state() == StepState.PENDING
    with pytest.raises(AssertionError):
        wfp.update_file_hashes([("foo.txt", fake_hash("foo.txt"))], HashUpdateCause.EXTERNAL)
    assert step.get_state() == StepState.PENDING


def test_externally_deleted_built_detached(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    wfp.define_step(plan, "touch foo.txt", out_paths=["foo.txt"])
    step = wfp.find(Step, "touch foo.txt")
    step.detach()
    assert step.get_state() == StepState.PENDING
    with pytest.raises(AssertionError):
        wfp.update_file_hashes([("foo.txt", FileHash.unknown())], HashUpdateCause.EXTERNAL)
    assert step.get_state() == StepState.PENDING


def test_directory_usage(wfp: Workflow):
    assert wfp.dir_queue.get_nowait() == "."
    assert wfp.dir_queue.empty()
    declare_static(wfp, wfp.root, ["foo.txt"])
    assert wfp.dir_queue.get_nowait() == "."
    assert wfp.dir_queue.empty()
    declare_static(wfp, wfp.root, ["sub/bar.txt"])
    assert wfp.dir_queue.get_nowait() == "sub"
    assert wfp.dir_queue.empty()
    for path in "sub/bar.txt", "foo.txt":
        wfp.find(File, path).detach()
        assert wfp.dir_queue.empty()
    wfp.clean()
    events = []
    while not wfp.dir_queue.empty():
        events.append(wfp.dir_queue.get_nowait())
    assert events == []


def test_to_be_deleted(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    declare_static(wfp, plan, ["static"])
    wfp.define_step(plan, "blub1", out_paths=["built", "gone"])
    blub1 = wfp.find(Step, "blub1")
    wfp.define_step(plan, "blub2", vol_paths=["volatile"])
    wfp.define_step(plan, "blub3", out_paths=["pending"])
    wfp.define_step(plan, "echo sub/foo", out_paths=["sub/foo"])
    built_file_hash = fake_hash("built")
    gone_file_hash = fake_hash("mockg")
    foo_file_hash = fake_hash("sub/foo")
    wfp.update_file_hashes(
        [("built", built_file_hash), ("gone", gone_file_hash), ("sub/foo", foo_file_hash)],
        HashUpdateCause.SUCCEEDED,
    )
    blub1.completed(StepHash(b"aaa", None, b"zzz", None))
    plan.detach()
    assert wfp.to_be_deleted == []
    assert wfp.find_detached(Step, "./plan.py") == (plan, True)
    wfp.clean()
    assert wfp.to_be_deleted == [
        ("built", built_file_hash),
        ("gone", gone_file_hash),
        ("volatile", None),
        ("sub/foo", foo_file_hash),
    ]
    assert wfp.find_detached(Step, "./plan.py") == (None, None)


def test_externally_deleted(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    (tst,) = declare_static(wfp, wfp.root, ["tst"])
    wfp.define_step(plan, "bla1", out_paths=["prr"])
    step1 = wfp.find(Step, "bla1")
    wfp.define_step(plan, "bla2", inp_paths=["prr"])
    step2 = wfp.find(Step, "bla2")

    # Static
    wfp.update_file_hashes([("tst", FileHash.unknown())], HashUpdateCause.EXTERNAL)
    assert tst.get_state() == FileState.MISSING
    with pytest.raises(AssertionError):
        wfp.update_file_hashes([("tst", FileHash.unknown())], HashUpdateCause.EXTERNAL)

    # Built
    prr = wfp.find(File, "prr")
    assert prr.get_state() == FileState.AWAITED
    with pytest.raises(AssertionError):
        wfp.update_file_hashes([("prr", FileHash.unknown())], HashUpdateCause.EXTERNAL)
    assert prr.get_state() == FileState.AWAITED
    wfp.update_file_hashes([("prr", fake_hash("prr"))], HashUpdateCause.SUCCEEDED)
    step1.completed(StepHash(b"11", None, b"zzz", None))
    step2.completed(None)
    assert prr.get_state() == FileState.BUILT
    wfp.update_file_hashes([("prr", FileHash.unknown())], HashUpdateCause.EXTERNAL)
    assert prr.get_state() == FileState.AWAITED
    assert step1.get_state() == StepState.PENDING
    assert step2.get_state() == StepState.PENDING


def test_externally_updated2(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    (tst,) = declare_static(wfp, wfp.root, ["tst"])
    wfp.define_step(plan, "cat tst", inp_paths=["tst"])
    cat = wfp.find(Step, "cat tst")
    wfp.define_step(plan, "bla1", out_paths=["prr"])
    step1 = wfp.find(Step, "bla1")
    wfp.define_step(plan, "bla2", inp_paths=["prr"])
    step2 = wfp.find(Step, "bla2")

    # Static
    cat.completed(StepHash(b"sfdsafds", None, b"zzz", None))
    wfp.update_file_hashes([("tst", FileHash.unknown())], HashUpdateCause.EXTERNAL)
    assert tst.get_state() == FileState.MISSING
    assert cat.get_state() == StepState.PENDING
    wfp.update_file_hashes([("tst", fake_hash("tst"))], HashUpdateCause.EXTERNAL)
    assert tst.get_state() == FileState.STATIC
    assert cat.get_state() == StepState.PENDING

    # Built
    prr = wfp.find(File, "prr")
    assert prr.get_state() == FileState.AWAITED
    wfp.update_file_hashes([("prr", fake_hash("prr"))], HashUpdateCause.SUCCEEDED)
    step1.completed(StepHash(b"11", None, b"zzz", None))
    step2.completed(None)
    assert prr.get_state() == FileState.BUILT
    assert step2.get_state() == StepState.FAILED
    wfp.update_file_hashes([("prr", FileHash.unknown())], HashUpdateCause.EXTERNAL)
    assert prr.get_state() == FileState.AWAITED
    assert step1.get_state() == StepState.PENDING
    assert step2.get_state() == StepState.PENDING


def test_step_recycle(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    wfp.define_step(plan, "echo foo > bar", out_paths=["bar"])
    echo = wfp.find(Step, "echo foo > bar")
    step_hash = StepHash(b"bsfssfdsdfsdfasdfasa", None, b"zzz", None)
    wfp.update_file_hashes([("bar", fake_hash("bar"))], HashUpdateCause.SUCCEEDED)
    echo.completed(step_hash)
    hash1 = echo.get_hash()
    assert hash1 is not None

    # Detach and recycle
    echo.detach()
    wfp.define_step(plan, "echo foo > bar", out_paths=["bar"])
    hash2 = echo.get_hash()
    assert hash2 is not None
    assert hash1.inp_digest == hash2.inp_digest
    assert hash1.out_digest == hash2.out_digest


def test_output_clean_nested(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    wfp.define_step(plan, "echo egg > s/foo/bar/egg", out_paths=["s/foo/bar/egg"])
    step = wfp.find(Step, "echo egg > s/foo/bar/egg")
    wfp.clean()
    f, detached = wfp.find_detached(File, "s/foo/bar/egg")
    assert isinstance(f, File)
    assert not detached
    assert f.creator().i == step.i

    step.detach()
    f, detached = wfp.find_detached(File, "s/foo/bar/egg")
    assert isinstance(f, File)
    assert detached

    wfp.clean()
    assert wfp.find_detached(File, "s/foo/bar/egg") == (None, None)


def test_clean_multiple_suppliers(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    (file,) = declare_static(wfp, plan, ["common.txt"])
    wfp.define_step(plan, "prog1 common.txt", inp_paths=["common.txt"], out_paths=["output1.txt"])
    step1 = wfp.find(Step, "prog1 common.txt")
    wfp.define_step(plan, "prog2 common.txt", inp_paths=["common.txt"], out_paths=["output2.txt"])
    step2 = wfp.find(Step, "prog2 common.txt")
    file.detach()
    wfp.clean()
    assert file.is_detached()
    step1.detach()
    wfp.clean()
    assert file.is_detached()
    step2.detach()
    wfp.clean()
    assert wfp.find_detached(File, "common.txt") == (None, None)


def test_env_vars(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    wfp.define_step(plan, "prog1", env_vars=["name", "other"])
    step = wfp.find(Step, "prog1")
    assert set(step.env_vars(amended=False)) == {"name", "other"}
    assert set(step.env_vars(amended=True)) == set()
    assert set(step.env_vars()) == {"name", "other"}


def test_amended_env_vars(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    wfp.define_step(plan, "prog1", env_vars=["egg"])
    step = wfp.find(Step, "prog1")
    wfp.amend_step(step, env_vars=["foo", "egg"])
    wfp.amend_step(step, env_vars=["foo", "bar"])
    assert set(step.env_vars(amended=False)) == {"egg"}
    assert set(step.env_vars(amended=True)) == {"bar", "foo"}
    assert set(step.env_vars()) == {"bar", "egg", "foo"}


def test_acyclic_amend_static(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    declare_static(wfp, plan, ["static.txt"])
    wfp.amend_step(plan, inp_paths=["static.txt"])
    assert set(plan.inp_paths()) == {"plan.py", "static.txt"}
    assert set(plan.static_paths()) == {"static.txt"}


def test_cyclic_two_steps(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    wfp.define_step(plan, "cat first > second", inp_paths=["first"], out_paths=["second"])
    with pytest.raises(GraphError):
        wfp.define_step(plan, "cat second > first", inp_paths=["second"], out_paths=["first"])


def test_static_tree_basic(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    # Define a step with a detached input
    to_check = wfp.define_step(plan, "cat head/one.txt", inp_paths=["head/one.txt"])
    assert to_check == []

    # Define static tree and check attributes
    with pytest.raises(ValueError):
        wfp.register_static_tree(plan, "head*")
    to_check_h = wfp.register_static_tree(plan, "head")
    to_check_t = wfp.register_static_tree(plan, "tail")
    assert isinstance(wfp.find(StaticTree, "head/"), StaticTree)
    assert isinstance(wfp.find(StaticTree, "tail/"), StaticTree)

    # Validate the to_check result
    assert to_check_h == [("head/one.txt", FileHash.unknown())]
    assert to_check_t == []
    head1 = wfp.find(File, "head/one.txt")
    assert head1.get_state() == FileState.MISSING

    # Check if head_1.txt is static after confirming
    wfp.update_file_hashes([("head/one.txt", fake_hash("head/one.txt"))], HashUpdateCause.CONFIRMED)
    assert head1.get_state() == FileState.STATIC

    # Use static tree after it is added
    to_check = wfp.define_step(plan, "cat tail/one.txt", inp_paths=["tail/one.txt"])
    assert to_check == [("tail/one.txt", FileHash.unknown())]
    tail1 = wfp.find(File, "tail/one.txt")
    assert tail1.get_state() == FileState.MISSING
    with pytest.raises(AssertionError):
        wfp.update_file_hashes(to_check, HashUpdateCause.CONFIRMED)
    wfp.update_file_hashes([("tail/one.txt", fake_hash("tail/one.txt"))], HashUpdateCause.CONFIRMED)
    assert tail1.get_state() == FileState.STATIC


def test_static_tree_clean(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    to_check = wfp.register_static_tree(plan, "static")
    assert len(to_check) == 0
    inp_paths = ["static/foo/bar.txt"]
    to_check = wfp.define_step(plan, "cat static/foo/bar.txt", inp_paths=inp_paths)
    assert to_check == [("static/foo/bar.txt", FileHash.unknown())]
    wfp.update_file_hashes(
        [
            ("static/foo/bar.txt", fake_hash("static/foo/bar.txt")),
        ],
        HashUpdateCause.CONFIRMED,
    )
    step = wfp.find(Step, "cat static/foo/bar.txt")

    # Check effect of defining the step on the static tree
    assert wfp.find(File, "static/foo/bar.txt").get_state() == FileState.STATIC

    # Simulate the execution of the steps
    plan.completed(StepHash(b"sthp", None, b"zzz", None))
    step.completed(StepHash(b"sths", None, b"zzz", None))

    # Check the hashes
    assert plan.get_hash().inp_digest == b"sthp"
    assert step.get_hash().inp_digest == b"sths"

    # Detach the step, manually outdate it, clean and check result
    step.detach()
    wfp.clean()
    sr = wfp.find(StaticTree, "static/")
    assert sr.creator().i == plan.i
    assert not step.is_alive()
    assert wfp.find_detached(File, "static") == (None, None)
    assert wfp.find_detached(File, "static/") == (None, None)
    assert wfp.find_detached(File, "static/foo") == (None, None)
    assert wfp.find_detached(File, "static/foo/") == (None, None)
    assert wfp.find_detached(File, "static/foo/bar.txt") == (None, None)

    # make the plan pending
    plan.mark_pending()
    assert not sr.is_detached()
    assert plan.get_state() == StepState.PENDING


def test_static_tree_subdir(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    wfp.register_static_tree(plan, "static/sub")
    with pytest.raises(GraphError):
        wfp.register_static_tree(plan, "static")
    with pytest.raises(GraphError):
        wfp.register_static_tree(plan, "static/sub/dir")


def test_static_tree_static(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    wfp.register_static_tree(plan, "static")
    with pytest.raises(GraphError):
        declare_static(wfp, plan, ["static/README.md"])


def test_static_tree_output(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    wfp.register_static_tree(plan, "static")
    with pytest.raises(GraphError):
        wfp.define_step(plan, "echo foo > static/README.md", out_paths=["static/README.md"])


def test_static_tree_volatile(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    wfp.register_static_tree(plan, "static")
    with pytest.raises(GraphError):
        wfp.define_step(plan, "echo foo > static/README.md", vol_paths=["static/README.md"])


def test_orhphaned_static_tree(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    wfp.register_static_tree(plan, "part_a")
    wfp.register_static_tree(plan, "part_b")
    wfp.find(StaticTree, "part_a/").detach()
    to_check = wfp.define_step(plan, "prog", inp_paths=["part_a/README.md", "part_b/README.md"])
    assert to_check == [("part_b/README.md", FileHash.unknown())]


def test_static_tree_amend_inp(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    wfp.register_static_tree(plan, "static")
    to_check = wfp.define_step(plan, "prog", inp_paths=["static/initial.md", "other/initial.md"])
    assert to_check == [("static/initial.md", FileHash.unknown())]
    prog = wfp.find(Step, "prog")
    keep_going, to_check = wfp.amend_step(prog, inp_paths=["static/other.md"])
    assert keep_going
    assert to_check == [("static/other.md", FileHash.unknown())]
    keep_going, to_check = wfp.amend_step(prog, inp_paths=["static/amended.md", "other/amended.md"])
    assert not keep_going
    assert to_check == [("static/amended.md", FileHash.unknown())]


def test_static_tree_amend_out(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    wfp.register_static_tree(plan, "data")
    to_check = wfp.define_step(plan, "prog", inp_paths=["data/somefile.txt"])
    assert to_check == [("data/somefile.txt", FileHash.unknown())]
    prog = wfp.find(Step, "prog")
    with pytest.raises(GraphError):
        wfp.amend_step(prog, vol_paths=["data/vol_amended/vol.txt"])
    with pytest.raises(GraphError):
        wfp.amend_step(prog, out_paths=["data/out_amended/out.txt"])


def test_static_tree_recursive(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    wfp.register_static_tree(plan, "data")
    to_check = wfp.define_step(plan, "prog", inp_paths=["data/foo/a/bar.txt", "data/foo/b/egg.txt"])
    assert to_check == [
        ("data/foo/a/bar.txt", FileHash.unknown()),
        ("data/foo/b/egg.txt", FileHash.unknown()),
    ]


def test_define_step_reqdir_out_path(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    wfp.define_step(plan, "echo", out_paths=["sub/dir/out"])
    reqdir, detached = wfp.find_detached(File, "sub/dir")
    assert reqdir is None
    assert detached is None


def test_define_step_reqdir_vol_path(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    wfp.define_step(plan, "echo", vol_paths=["sub/dir/vol"])
    reqdir, detached = wfp.find_detached(File, "sub/dir")
    assert reqdir is None
    assert detached is None


def test_define_step_reqdir_workdir(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    wfp.define_step(plan, "echo", workdir="sub/dir")
    echo = wfp.find(Step, "echo  # wd=sub/dir")
    command, workdir = echo.get_command_workdir()
    assert command == "echo"
    assert workdir == Path("sub/dir")
    assert isinstance(workdir, Path)
    reqdir, detached = wfp.find_detached(File, "sub/dir")
    assert reqdir is None
    assert detached is None


def test_amend_step_reqdir_out_path(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    wfp.define_step(plan, "echo")
    step = wfp.find(Step, "echo")
    wfp.amend_step(step, out_paths=["sub/dir/out"])
    reqdir, detached = wfp.find_detached(File, "sub/dir")
    assert reqdir is None
    assert detached is None


def test_amend_step_reqdir_vol_path(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    wfp.define_step(plan, "echo")
    step = wfp.find(Step, "echo")
    wfp.amend_step(step, vol_paths=["sub/dir/vol"])
    reqdir, detached = wfp.find_detached(File, "sub/dir")
    assert reqdir is None
    assert detached is None


def test_define_step_directory_input_disallowed(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    with pytest.raises(GraphError, match="Directory inputs are not supported"):
        wfp.define_step(plan, "echo", inp_paths=["sub/"])


def test_inp_paths(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    wfp.define_step(plan, "script", inp_paths=["foo"])
    step = wfp.find(Step, "script")
    assert set(step.inp_paths()) == set()
    assert set(step.inp_paths(yield_detached=True)) == {("foo", True)}
    assert list(step.inp_paths(yield_state=True)) == []
    assert set(step.inp_paths(yield_state=True, yield_detached=True)) == {
        ("foo", FileState.AWAITED, True),
    }
    assert list(step.inp_paths(yield_hash=True)) == []


def test_out_paths(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    wfp.define_step(plan, "script", out_paths=["foo", "bar"])
    step = wfp.find(Step, "script")
    wfp.update_file_hashes([("bar", fake_hash("bar"))], HashUpdateCause.SUCCEEDED)
    assert set(step.out_paths()) == {"bar", "foo"}
    assert set(step.out_paths(yield_state=True)) == {
        ("bar", FileState.BUILT),
        ("foo", FileState.AWAITED),
    }
    assert sorted(step.out_paths(yield_hash=True)) == [
        ("bar", fake_hash("bar")),
        ("foo", FileHash.unknown()),
    ]
    assert sorted(step.out_paths(yield_state=True, yield_hash=True)) == [
        ("bar", FileState.BUILT, fake_hash("bar")),
        ("foo", FileState.AWAITED, FileHash.unknown()),
    ]


def test_vol_paths(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    wfp.define_step(plan, "script", vol_paths=["foo", "bar"])
    step = wfp.find(Step, "script")
    assert set(step.vol_paths()) == {"bar", "foo"}
    assert sorted(step.vol_paths(yield_hash=True)) == [
        ("bar", FileHash.unknown()),
        ("foo", FileHash.unknown()),
    ]


def test_static_missing_paths(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    wfp.define_step(plan, "script")
    step = wfp.find(Step, "script")
    declare_static(wfp, step, ["foo", "bar", "zzz"])
    wfp.find(File, "zzz").set_state(FileState.MISSING)
    assert set(step.static_paths()) == {"bar", "foo"}
    assert set(step.missing_paths()) == {"zzz"}
    assert set(wfp.missing_paths()) == {"zzz"}
    assert sorted(step.static_paths(yield_hash=True)) == [
        ("bar", fake_hash("bar")),
        ("foo", fake_hash("foo")),
    ]
    assert list(step.missing_paths(yield_hash=True)) == [("zzz", FileHash.unknown())]


def test_skip_amend_detached_inputs(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    wfp.define_step(plan, "prog", out_paths=["bar"])
    step = wfp.find(Step, "prog")
    (foo1,) = declare_static(wfp, plan, ["foo"])

    # Simulate running the step, which amends a few things.
    wfp.amend_step(step, inp_paths=["foo"], env_vars=["AAA"], vol_paths=["bbb"])
    assert set(step.inp_paths(yield_detached=True, yield_amended=True)) == {("foo", False, True)}
    assert set(step.env_vars(yield_amended=True)) == {("AAA", True)}
    assert set(step.out_paths(yield_detached=True, yield_amended=True)) == {
        ("bar", False, False),
    }
    assert set(step.vol_paths(yield_detached=True, yield_amended=True)) == {
        ("bbb", False, True),
    }
    wfp.update_file_hashes([("bar", fake_hash("bar"))], HashUpdateCause.SUCCEEDED)
    step.completed(StepHash(b"step_ok", None, b"zzz", None))
    assert step.get_state() == StepState.SUCCEEDED
    assert step.get_hash() is not None

    # Detach the static input.
    foo1.detach()
    assert foo1.is_detached()
    # Amended info is not removed
    assert set(step.inp_paths(yield_detached=True, yield_amended=True)) == {("foo", True, True)}
    assert set(step.env_vars(yield_amended=True)) == {("AAA", True)}
    assert set(step.out_paths(yield_detached=True, yield_amended=True)) == {
        ("bar", False, False),
    }
    assert set(step.vol_paths(yield_detached=True, yield_amended=True)) == {
        ("bbb", False, True),
    }

    # Detach step1
    step.detach()
    assert step.is_alive()
    assert step.is_detached()
    assert step.get_hash() is not None

    # Redefine the step in exactly the same way
    (foo2,) = declare_static(wfp, plan, ["foo"])
    assert foo1 == foo2
    wfp.define_step(plan, "prog", out_paths=["bar"])
    assert not step.is_detached()
    assert set(step.inp_paths()) == {"foo"}
    assert set(step.inp_paths(yield_detached=True)) == {("foo", False)}
    assert set(step.out_paths()) == {"bar"}
    # Note that amended info is removed when inputs of a step are detached.
    assert set(step.vol_paths()) == {"bbb"}

    # Check that amended info is back and hash is still in place
    assert step.get_hash() is not None


def test_define_step_out_nested(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    wfp.define_step(plan, "script", out_paths=["sub/foo/bar"])
    step = wfp.find(Step, "script")
    assert set(step.inp_paths()) == set()
    assert set(step.out_paths()) == {"sub/foo/bar"}


def test_define_step_vol_nested(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    wfp.define_step(plan, "script", vol_paths=["sub/foo/bar"])
    step = wfp.find(Step, "script")
    assert set(step.inp_paths()) == set()
    assert set(step.out_paths()) == set()
    assert set(step.vol_paths()) == {"sub/foo/bar"}


def test_amend_step_out_nested(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    wfp.define_step(plan, "script")
    step = wfp.find(Step, "script")
    wfp.amend_step(step, out_paths=["sub/foo/bar"])
    assert set(step.inp_paths()) == set()
    assert set(step.out_paths()) == {"sub/foo/bar"}


def test_amend_step_vol_nested(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    wfp.define_step(plan, "script")
    step = wfp.find(Step, "script")
    wfp.amend_step(step, vol_paths=["sub/foo/bar"])
    assert set(step.inp_paths()) == set()
    assert set(step.out_paths()) == set()
    assert set(step.vol_paths()) == {"sub/foo/bar"}


def test_step_static_tree(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    assert wfp.register_static_tree(plan, "sub") == []
    inp_paths = ["test.png", "test.txt", "other.txt", "sub/boom.txt", "sub/README.md"]
    to_check = wfp.define_step(plan, "prog", inp_paths=inp_paths)
    assert to_check == [
        ("sub/README.md", FileHash.unknown()),
        ("sub/boom.txt", FileHash.unknown()),
    ]

    # Check file nodes
    for path in "test.png", "test.txt", "other.txt":
        file, detached = wfp.find_detached(File, path)
        assert detached
        assert file.get_state() == FileState.AWAITED
    for path in "sub/boom.txt", "sub/README.md":
        file, detached = wfp.find_detached(File, path)
        assert not detached
        assert file.get_state() == FileState.MISSING


def test_confirm_missing(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    wfp.define_step(plan, "cat ${inp}", inp_paths=["test.txt"])
    to_check = wfp.declare_missing(plan, ["test.txt", "other.txt"])
    assert to_check == [("other.txt", FileHash.unknown()), ("test.txt", FileHash.unknown())]
    # static other.txt
    assert wfp.find(File, "other.txt").get_state() == FileState.MISSING
    wfp.update_file_hashes([("other.txt", fake_hash("other.txt"))], HashUpdateCause.CONFIRMED)
    assert wfp.find(File, "other.txt").get_state() == FileState.STATIC
    # static test.txt
    assert wfp.find(File, "test.txt").get_state() == FileState.MISSING
    wfp.update_file_hashes([("test.txt", fake_hash("test.txt"))], HashUpdateCause.CONFIRMED)
    assert wfp.find(File, "test.txt").get_state() == FileState.STATIC


def test_step_try_clean(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")

    # Simulate execution of plan to get a hash
    step_hash = StepHash(b"p" * 32, None, b"p" * 32, None)
    plan.completed(step_hash)

    # Check presence of hash
    assert plan.get_hash() == step_hash

    # Run try_clean (via clean) and verify that hash has been removed.
    plan.detach()
    wfp.clean()
    assert plan.get_hash() is None


def test_step_lost_child(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    wfp.define_step(plan, "prog", out_paths=["data.txt"])
    step = wfp.find(Step, "prog")
    step.detach()
    assert step.is_detached()

    # Simulate creation of new data.txt
    to_check = wfp.declare_missing(wfp.root, ["data.txt"])
    assert to_check == [("data.txt", FileHash.unknown())]
    data = wfp.find(File, "data.txt")
    assert data.creator() == wfp.root

    # Check that step of prog is gone
    assert list(wfp.nodes(Step, include_detached=True)) == [plan]


def test_static_tree_lost_child(wfp: Workflow):
    # Construct a workflow with a statoc root
    plan = wfp.find(Step, "./plan.py")
    wfp.define_step(plan, "prog")
    prog = wfp.find(Step, "prog")
    wfp.register_static_tree(prog, "data")

    # Simulate the creation of a static data/foo.txt through the static tree.
    to_check = wfp.define_step(prog, "work", inp_paths=["data/foo.txt"])
    assert to_check == [("data/foo.txt", FileHash.unknown())]
    wfp.update_file_hashes([("data/foo.txt", fake_hash("data/foo.txt"))], HashUpdateCause.CONFIRMED)

    prog.detach()
    assert prog.is_detached()

    # Simulate creation of new data/foo.txt
    to_check = wfp.declare_missing(wfp.root, ["data/foo.txt"])
    assert to_check == [("data/foo.txt", FileHash.unknown())]
    wfp.update_file_hashes([("data/foo.txt", fake_hash("data/foo.txt"))], HashUpdateCause.CONFIRMED)
    data = wfp.find(File, "data/foo.txt")
    assert data.creator() == wfp.root

    # Check that step of prog is gone
    assert list(wfp.nodes(StaticTree, include_detached=True)) == []


def test_consistency_parent(wfp: Workflow):
    declare_static(wfp, wfp.find(Step, "./plan.py"), ["local.txt"])
    # Manually change local.txt to sub/local.txt
    wfp.con.execute("UPDATE node SET label = 'sub/local.txt' WHERE label = 'local.txt'")
    wfp.check_consistency()
    # Manually set it back, because wfp will get checked by fixture.
    wfp.con.execute("UPDATE node SET label = 'local.txt' WHERE label = 'sub/local.txt'")


def test_consistency_succeeded_step(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    wfp.define_step(plan, "prog", out_paths=["out.txt"])
    step = wfp.find(Step, "prog")
    wfp.update_file_hashes([("out.txt", fake_hash("out.txt"))], HashUpdateCause.SUCCEEDED)
    step.completed(StepHash(b"prog", None, b"zzz", None))
    assert step.get_state() == StepState.SUCCEEDED
    out = wfp.find(File, "out.txt")
    assert out.get_state() == FileState.BUILT
    file_hashes = wfp.get_file_hashes(["out.txt"])
    assert file_hashes == [("out.txt", fake_hash("out.txt"))]
    # Manually change the output file to AWAITED, which must clear the file hash.
    # However, this is still the output of a BUILT step, which should trip the consistency check.
    with pytest.raises(GraphError), wfp.con:  # noqa: PT012
        out.set_state(FileState.AWAITED)
        file_hashes = wfp.get_file_hashes(["out.txt"])
        wfp.check_consistency()
    assert file_hashes == [("out.txt", FileHash.unknown())]


def test_sql_recurse_products_pending_tree(wfp: Workflow):
    # Create a tree of steps
    plan = wfp.find(Step, "./plan.py")
    wfp.define_step(plan, "foo", inp_paths=["data.txt"])
    foo = wfp.find(Step, "foo")
    assert foo.get_state() == StepState.PENDING
    wfp.define_step(foo, "bar")
    bar = wfp.find(Step, "bar")
    assert bar.get_state() == StepState.PENDING
    wfp.define_step(bar, "egg", inp_paths=["data.txt"])
    egg = wfp.find(Step, "egg")
    assert egg.get_state() == StepState.PENDING
    wfp.define_step(bar, "spam")
    spam = wfp.find(Step, "spam")
    assert spam.get_state() == StepState.PENDING
    wfp.define_step(spam, "step1", inp_paths=["data.txt"])
    step1 = wfp.find(Step, "step1")
    assert step1.get_state() == StepState.PENDING
    wfp.define_step(spam, "step2", inp_paths=["data.txt"])
    step2 = wfp.find(Step, "step2")
    assert step2.get_state() == StepState.PENDING

    # Set the states so that there should be two pending steps that are potentially queuable.
    foo.set_state(StepState.RUNNING)
    bar.set_state(StepState.SUCCEEDED)
    spam.set_state(StepState.RUNNING)


def test_recurse_outdated_steps1(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    wfp.define_step(plan, "prog", inp_paths=["data.txt"])
    prog = wfp.find(Step, "prog")
    rows = wfp.con.execute(RECURSE_OUTDATED_STEPS, (prog.i,)).fetchall()
    assert len(rows) == 1
    assert Step(wfp, *rows[0]) == prog


def test_recurse_outdated_steps2(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    wfp.define_step(plan, "prog1", inp_paths=["data1.txt"])
    prog1 = wfp.find(Step, "prog1")
    wfp.define_step(prog1, "prog2", inp_paths=["data2.txt"])
    prog2 = wfp.find(Step, "prog2")
    wfp.define_step(prog1, "prog3", inp_paths=["data3.txt"])
    declare_static(wfp, plan, ["data3.txt"])

    rows = wfp.con.execute(RECURSE_OUTDATED_STEPS, (prog1.i,)).fetchall()
    assert len(rows) == 2
    assert Step(wfp, *rows[0]) == prog1
    assert Step(wfp, *rows[1]) == prog2


@pytest.mark.parametrize("inp_path", ["data/foo.txt", "data/sub/deep.txt", "data/sub/a/deep.txt"])
def test_recurse_deferred_inputs1(wfp: Workflow, inp_path: str):
    plan = wfp.find(Step, "./plan.py")
    wfp.define_step(plan, "prog", inp_paths=[inp_path])
    prog = wfp.find(Step, "prog")
    wfp.register_static_tree(plan, "data")
    rows = wfp.con.execute(RECURSE_DEFERRED_INPUTS, (prog.i,)).fetchall()
    assert len(rows) == 1
    data = wfp.find(File, inp_path)
    assert File(wfp, *rows[0]) == data


def test_recreate_step_to_check(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    wfp.register_static_tree(plan, "data")
    to_check = wfp.define_step(plan, "prog", inp_paths=["data/foo.txt"])
    assert to_check == [("data/foo.txt", FileHash.unknown())]
    prog = wfp.find(Step, "prog")
    prog.detach()
    to_check = wfp.define_step(plan, "prog", inp_paths=["data/foo.txt"])
    assert to_check == [("data/foo.txt", FileHash.unknown())]


def test_recreate_step_to_check_amend(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    wfp.register_static_tree(plan, "static")
    to_check = wfp.define_step(
        plan,
        "prog",
        inp_paths=["static/inp1.txt"],
        out_paths=["out1.txt"],
        vol_paths=["vol1.txt"],
    )
    assert to_check == [("static/inp1.txt", FileHash.unknown())]
    wfp.update_file_hashes(
        [("static/inp1.txt", fake_hash("static/inp1.txt"))], HashUpdateCause.CONFIRMED
    )
    prog = wfp.find(Step, "prog")
    prog.detach()
    to_check = wfp.define_step(
        plan,
        "prog",
        inp_paths=["static/inp1.txt"],
        out_paths=["out1.txt"],
        vol_paths=["vol1.txt"],
    )
    assert to_check == []
    keep_going, to_check = wfp.amend_step(
        prog, inp_paths=["other/inp2.txt"], out_paths=["out2.txt"], vol_paths=["vol2.txt"]
    )
    assert not keep_going
    assert to_check == []


def test_get_file_hashes(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    paths = ["data.txt", "other.txt"]
    wfp.declare_missing(plan, paths)
    assert wfp.get_file_hashes(paths) == [
        ("data.txt", FileHash.unknown()),
        ("other.txt", FileHash.unknown()),
    ]
    wfp.update_file_hashes([("data.txt", fake_hash("data.txt"))], HashUpdateCause.CONFIRMED)
    assert wfp.get_file_hashes(paths) == [
        ("data.txt", fake_hash("data.txt")),
        ("other.txt", FileHash.unknown()),
    ]


def test_add_rescheduled_info(wfs: Workflow):
    wfs.define_step(wfs.root, "echo")
    echo = wfs.find(Step, "echo")
    echo.add_rescheduled_info("because, like...\n...you know, right")
    assert echo.get_rescheduled_info().splitlines() == ["because, like...", "...you know, right"]
    echo.add_rescheduled_info("you know what...\n...I mean, come on!")
    assert echo.get_rescheduled_info().splitlines() == [
        "because, like...",
        "...you know, right",
        "you know what...",
        "...I mean, come on!",
    ]


def test_clean_stepup_root_parents(wfs: Workflow):
    declare_static(wfs, wfs.root, ["../foo.txt"])
    assert wfs.find(File, "../foo.txt").get_state() == FileState.STATIC
    wfs.clean()
    assert wfs.find(File, "../foo.txt").get_state() == FileState.STATIC


def test_large_inode(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    large_inode = 0x8000000000000001
    wfp.declare_missing(plan, ["foo.txt"])
    wfp.update_file_hashes(
        [("foo.txt", FileHash(hashlib.sha256(b"foo").digest(), 0o644, 1.0, 10, large_inode))],
        HashUpdateCause.CONFIRMED,
    )
    foo = wfp.find(File, "foo.txt")
    hash_info = foo.get_hash()
    assert hash_info is not None
    assert hash_info.inode == large_inode


@pytest.mark.parametrize(
    "resources",
    [
        {"gpu": 0},
        {"gpu": -1},
        {"": 1},
    ],
)
def test_define_step_invalid_resources(wfp: Workflow, resources: dict):
    plan = wfp.find(Step, "./plan.py")
    with pytest.raises(sqlite3.IntegrityError), wfp.con:
        wfp.define_step(plan, "echo", resources=resources)


def test_step_output_roundtrip(wfp: Workflow):
    """store_output / get_output / delete_outputs round-trip each stream independently."""
    step = wfp.find(Step, "./plan.py")

    # Empty content stores no row.
    step.store_output("stdout", "", 0)
    assert step.get_output("stdout") == ""

    # Non-empty content round-trips, each stream independently.
    step.store_output("stdout", "hello out\n", 0)
    step.store_output("stderr", "hello err\n", 0)
    assert step.get_output("stdout") == "hello out\n"
    assert step.get_output("stderr") == "hello err\n"

    # Re-storing empty content clears the stale row without touching the other stream.
    step.store_output("stdout", "", 0)
    assert step.get_output("stdout") == ""
    assert step.get_output("stderr") == "hello err\n"

    # delete_outputs removes all kinds at once.
    step.store_output("stdout", "again\n", 0)
    step.delete_outputs()
    assert step.get_output("stdout") == ""
    assert step.get_output("stderr") == ""


def test_step_output_truncated_on_store(wfp: Workflow):
    """store_output applies the byte budget; get_output returns the truncated text."""
    step = wfp.find(Step, "./plan.py")
    step.store_output("stdout", "abcdefghij", 5)
    assert step.get_output("stdout") == "abcde\n[output truncated at 5 bytes]\n"


def test_step_output_give_up_no_fk_error(wfp: Workflow):
    """give_up() removes stored output via ON DELETE CASCADE when the node row is deleted."""
    plan = wfp.find(Step, "./plan.py")
    wfp.define_step(plan, "echo hi")
    step = wfp.find(Step, "echo hi")
    step.store_output("stdout", "data\n", 0)
    step.store_output("stderr", "oops\n", 0)
    # give_up() deletes the node row, and the step_output rows are removed automatically
    # by the ON DELETE CASCADE foreign key.
    step.give_up()
    assert step.get_output("stdout") == ""
    assert step.get_output("stderr") == ""


def test_step_subprocess_roundtrip(wfp: Workflow):
    """record_subprocess / iter_subprocesses round-trip cmd, workdir, env, returncode, shell."""
    step = wfp.find(Step, "./plan.py")

    # No records yet.
    assert list(step.iter_subprocesses()) == []

    # Record two invocations: one with an env overlay, a non-zero return code, and shell=False;
    # one without an overlay and shell=True.
    step.record_subprocess("typst compile a.typ a.pdf", "sub", {"TR": "/x"}, 7, False)
    step.record_subprocess("echo hi | tr a b", ".", None, 0, True)

    # They round-trip in seq order, with cmd stored verbatim and env decoded back to a dict.
    assert list(step.iter_subprocesses()) == [
        (0, "typst compile a.typ a.pdf", "sub", {"TR": "/x"}, 7, False),
        (1, "echo hi | tr a b", ".", None, 0, True),
    ]


def test_step_subprocess_clean_restarts_seq(wfp: Workflow):
    """delete_subprocesses removes all rows and the seq numbering restarts at 0."""
    step = wfp.find(Step, "./plan.py")
    step.record_subprocess("a", ".", None, 0)
    step.record_subprocess("b", ".", None, 0)
    assert [row[0] for row in step.iter_subprocesses()] == [0, 1]

    step.delete_subprocesses()
    assert list(step.iter_subprocesses()) == []

    # A fresh record after cleanup restarts the sequence at 0.
    step.record_subprocess("c", ".", None, 0)
    assert [row[0] for row in step.iter_subprocesses()] == [0]


def test_step_subprocess_clean_before_run(wfp: Workflow):
    """clean_before_run drops subprocess rows recorded by a previous run."""
    plan = wfp.find(Step, "./plan.py")
    wfp.define_step(plan, "echo hi")
    step = wfp.find(Step, "echo hi")
    step.record_subprocess("echo hi", ".", None, 0)
    assert len(list(step.iter_subprocesses())) == 1
    step.clean_before_run()
    assert list(step.iter_subprocesses()) == []


def test_step_subprocess_give_up_no_fk_error(wfp: Workflow):
    """give_up() removes recorded subprocesses (clean() runs before node deletion, no FK error)."""
    plan = wfp.find(Step, "./plan.py")
    wfp.define_step(plan, "echo hi")
    step = wfp.find(Step, "echo hi")
    step.record_subprocess("echo hi", ".", None, 0)
    # give_up() deletes the node row, and the step_subprocess rows are removed automatically
    # by the ON DELETE CASCADE foreign key.
    step.give_up()
    assert list(step.iter_subprocesses()) == []


# Satellite tables whose rows hang off a node and are removed by ON DELETE CASCADE.
SATELLITE_NODE_TABLES = (
    "step",
    "env_var",
    "step_hash",
    "step_resource",
    "step_output",
    "step_subprocess",
    "nglob_multi",
)


def test_clean_cascades_satellite_rows(wfs: Workflow):
    """Cleaning a node deletes all its satellite rows via ON DELETE CASCADE.

    No explicit per-table DELETE is issued in `Step.clean()` / `File.clean()` anymore;
    the cascade fires when `Cascade.clean()` deletes the node row.
    """
    # Foreign-key enforcement must be active on the connection or the cascades never fire.
    assert wfs.con.execute("PRAGMA foreign_keys").fetchone()[0] == 1

    # Build a step that owns a row in every satellite table, plus an output file.
    declare_static(wfs, wfs.root, ["inp.txt"])
    wfs.define_step(
        wfs.root,
        "do something",
        inp_paths=["inp.txt"],
        env_vars=["SOME_VAR"],
        out_paths=["out.txt"],
        resources={"cpu": 2},
    )
    step = wfs.find(Step, "do something")
    out_file = wfs.find(File, "out.txt")
    step.set_hash(StepHash(b"inp", None, b"out", None))
    step.store_output("stdout", "hello\n", 0)
    step.record_subprocess("do something", ".", None, 0)
    wfs.register_nglob(step, NGlobMulti.from_patterns(["*.txt"]))
    step_i = step.i
    out_i = out_file.i

    # Sanity check: a row exists in each satellite table and the output file table.
    for table in SATELLITE_NODE_TABLES:
        count = wfs.con.execute(f"SELECT count(*) FROM {table} WHERE node = ?", (step_i,))
        assert count.fetchone()[0] >= 1, f"expected a row in {table}"
    assert wfs.con.execute("SELECT count(*) FROM file WHERE node = ?", (out_i,)).fetchone()[0] == 1

    # Detach and clean: the step and its output file node are removed, cascading their rows.
    step.detach()
    wfs.clean()

    assert wfs.con.execute("SELECT count(*) FROM node WHERE i = ?", (step_i,)).fetchone()[0] == 0
    assert wfs.con.execute("SELECT count(*) FROM node WHERE i = ?", (out_i,)).fetchone()[0] == 0
    for table in SATELLITE_NODE_TABLES:
        count = wfs.con.execute(f"SELECT count(*) FROM {table} WHERE node = ?", (step_i,))
        assert count.fetchone()[0] == 0, f"orphan row left in {table}"
    assert wfs.con.execute("SELECT count(*) FROM file WHERE node = ?", (out_i,)).fetchone()[0] == 0
