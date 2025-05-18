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
# but WITHOUT ANY WARRANTY; without even the REQUIRED warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, see <http://www.gnu.org/licenses/>
#
# --
"""Unit tests for stepup.core.workflow."""

from collections import Counter

import pytest
from conftest import declare_static, fake_hash
from path import Path

from stepup.core.deferred_glob import DeferredGlob
from stepup.core.enums import DirWatch, FileState, Mandatory, StepState
from stepup.core.exceptions import GraphError
from stepup.core.file import File
from stepup.core.hash import FileHash, StepHash
from stepup.core.nglob import NGlobMulti
from stepup.core.step import HAS_UNCERTAIN_CREATORS, RECURSE_PRODUCTS_PENDING, Step
from stepup.core.workflow import RECURSE_DEFERRED_INPUTS, RECURSE_OUTDATED_STEPS, Workflow

TEST_FILE_GRAPH = """\
root:
             creates   file:./
             creates   file:script.sh

file:./
               state = STATIC
          created by   root:
            supplies   file:script.sh

file:script.sh
               state = STATIC
              digest = 899479a0 d1143d3b d98c3ad7 7f2131e6 7980074a 68e18831 41300d67 5e06985d
                     = fbe38c6f 8518c164 fddd51a7 efd34f8b cf1737f0 daad3bd3 c0c62048 330eab2e
          created by   root:
            consumes   file:./
"""


def test_file(wfs: Workflow):
    declare_static(wfs, wfs.root, ["script.sh"])
    assert wfs.format_str() == TEST_FILE_GRAPH
    file2 = wfs.find(File, "./")
    assert isinstance(file2, File)
    assert file2.path == "./"
    assert file2.key() == "file:./"
    assert file2.get_state() == FileState.STATIC
    file3 = wfs.find(File, "script.sh")
    assert isinstance(file3, File)
    assert file3.path == "script.sh"
    assert file3.key() == "file:script.sh"
    assert file3.get_state() == FileState.STATIC
    assert set(wfs.nodes(File)) == {file2, file3}

    # Verify things that should not be allowed
    with pytest.raises(GraphError):
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
             creates   file:./
             creates   step:cp foo.txt sub/bar.txt

file:./
               state = STATIC
          created by   root:
            supplies   (file:foo.txt)
            supplies   (file:sub/)
            supplies   step:cp foo.txt sub/bar.txt

step:cp foo.txt sub/bar.txt
               state = PENDING
          created by   root:
            consumes   (file:foo.txt)
            consumes   (file:sub/)
            consumes   file:./
             creates   file:sub/bar.txt
            supplies   file:sub/bar.txt

(file:sub/)
               state = AWAITED
            consumes   file:./
            supplies   file:sub/bar.txt
            supplies   step:cp foo.txt sub/bar.txt

(file:foo.txt)
               state = AWAITED
            consumes   file:./
            supplies   step:cp foo.txt sub/bar.txt

file:sub/bar.txt
               state = AWAITED
          created by   step:cp foo.txt sub/bar.txt
            consumes   (file:sub/)
            consumes   step:cp foo.txt sub/bar.txt
"""


TEST_STEP_GRAPH2 = """\
root:
             creates   file:./
             creates   step:cp foo.txt sub/bar.txt

file:./
               state = STATIC
          created by   root:
            supplies   (file:foo.txt)
            supplies   (file:spam.txt)
            supplies   (file:sub/)
            supplies   file:egg.csv
            supplies   step:cp foo.txt sub/bar.txt

step:cp foo.txt sub/bar.txt
               state = RUNNING
          created by   root:
            consumes   (file:foo.txt)
            consumes   (file:spam.txt) [amended]
            consumes   (file:sub/)
            consumes   file:./
             creates   file:egg.csv
             creates   file:sub/bar.txt
            supplies   file:egg.csv [amended]
            supplies   file:sub/bar.txt

(file:sub/)
               state = AWAITED
            consumes   file:./
            supplies   file:sub/bar.txt
            supplies   step:cp foo.txt sub/bar.txt

(file:foo.txt)
               state = AWAITED
            consumes   file:./
            supplies   step:cp foo.txt sub/bar.txt

file:sub/bar.txt
               state = AWAITED
          created by   step:cp foo.txt sub/bar.txt
            consumes   (file:sub/)
            consumes   step:cp foo.txt sub/bar.txt

(file:spam.txt)
               state = AWAITED
            consumes   file:./
            supplies   step:cp foo.txt sub/bar.txt

file:egg.csv
               state = AWAITED
          created by   step:cp foo.txt sub/bar.txt
            consumes   file:./
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
        action, workdir = step.get_action_workdir()
        assert action == "cp foo.txt sub/bar.txt"
        assert workdir == Path("./")
        assert isinstance(workdir, Path)
        assert wfs.format_str() == TEST_STEP_GRAPH
        assert list(wfs.nodes(Step)) == [step]
        assert wfs.dir_queue.get_nowait() == (DirWatch.START, "./")
        assert set(step.inp_paths(yield_orphan=True)) == {
            ("./", False),
            ("sub/", True),
            ("foo.txt", True),
        }
        assert set(step.out_paths()) == {"sub/bar.txt"}
        assert set(wfs.orphaned_inp_paths()) == {
            ("sub/", FileState.AWAITED),
            ("foo.txt", FileState.AWAITED),
        }

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
        assert step.get_rescheduled_info().splitlines() == [
            "Missing inputs and/or required directories:",
            "spam.txt",
        ]
        assert set(step.inp_paths(yield_orphan=True)) == {
            ("./", False),
            ("sub/", True),
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
             creates   file:./
             creates   step:cp foo.txt bar.txt

file:./
               state = STATIC
          created by   root:
            supplies   (file:foo.txt)
            supplies   file:bar.txt
            supplies   step:cp foo.txt bar.txt

step:cp foo.txt bar.txt
               state = PENDING
          created by   root:
            consumes   (file:foo.txt)
            consumes   file:./
             creates   file:bar.txt
            supplies   file:bar.txt

(file:foo.txt)
               state = AWAITED
            consumes   file:./
            supplies   step:cp foo.txt bar.txt

file:bar.txt
               state = AWAITED
          created by   step:cp foo.txt bar.txt
            consumes   file:./
            consumes   step:cp foo.txt bar.txt
"""

TEST_SIMPLE_EXAMPLE_GRAPH2 = """\
root:
             creates   file:./
             creates   file:foo.txt
             creates   step:cp foo.txt bar.txt

file:./
               state = STATIC
          created by   root:
            supplies   file:bar.txt
            supplies   file:foo.txt
            supplies   step:cp foo.txt bar.txt

step:cp foo.txt bar.txt
               state = QUEUED
          created by   root:
            consumes   file:./
            consumes   file:foo.txt
             creates   file:bar.txt
            supplies   file:bar.txt

file:foo.txt
               state = STATIC
              digest = c178fd84 a7dadcab 4bdb7d96 de15c388 148eda5b 0df7ad9e 7f8e2bf6 8f0e41a8
                     = e40c7bab 7fa20c52 b2a3a769 dbea6989 bc5a53eb 58c946fb daa2af53 4d4b8f33
          created by   root:
            consumes   file:./
            supplies   step:cp foo.txt bar.txt

file:bar.txt
               state = AWAITED
          created by   step:cp foo.txt bar.txt
            consumes   file:./
            consumes   step:cp foo.txt bar.txt
"""

TEST_SIMPLE_EXAMPLE_GRAPH3 = """\
root:
             creates   file:./
             creates   file:foo.txt
             creates   step:cp foo.txt bar.txt

file:./
               state = STATIC
          created by   root:
            supplies   file:bar.txt
            supplies   file:foo.txt
            supplies   step:cp foo.txt bar.txt

step:cp foo.txt bar.txt
               state = SUCCEEDED
          inp_digest = 17c1d560 3cf7bd4f 875210d7 6f1c045e ec1786c6 771bf4c9 9828f0fc 3c5ffc68
                     = ba2cf591 7c8fd611 1251a6a6 d282a090 3655e75e 44340b1a 4871a82d cb207b00
          out_digest = 392846e6 3d2af07d 9faa4b77 53f1e1c8 50ed5498 1c819a0b ea142875 4811867b
                     = 9006bd3f 67337d51 380f0d7f c06c4aec 5515067b 9161e066 c0bf7bcd 9a931d79
           explained = yes
          created by   root:
            consumes   file:./
            consumes   file:foo.txt
             creates   file:bar.txt
            supplies   file:bar.txt

file:foo.txt
               state = STATIC
              digest = c178fd84 a7dadcab 4bdb7d96 de15c388 148eda5b 0df7ad9e 7f8e2bf6 8f0e41a8
                     = e40c7bab 7fa20c52 b2a3a769 dbea6989 bc5a53eb 58c946fb daa2af53 4d4b8f33
          created by   root:
            consumes   file:./
            supplies   step:cp foo.txt bar.txt

file:bar.txt
               state = BUILT
              digest = 3a189c6b 58667ce2 d87de9df 940fc80c 2f70a873 79bce15c 6f5c8beb 0bac9682
                     = cebfa211 48fd8c69 9442301c 4b96735a bf03fa6d 4d8e0d46 9d089baa 4e67e198
          created by   step:cp foo.txt bar.txt
            consumes   file:./
            consumes   step:cp foo.txt bar.txt
"""

TEST_SIMPLE_EXAMPLE_GRAPH4 = """\
root:
             creates   file:./
             creates   file:foo.txt
             creates   step:cp foo.txt bar.txt

file:./
               state = STATIC
          created by   root:
            supplies   file:bar.txt
            supplies   file:foo.txt
            supplies   step:cp foo.txt bar.txt

step:cp foo.txt bar.txt
               state = PENDING
          inp_digest = 17c1d560 3cf7bd4f 875210d7 6f1c045e ec1786c6 771bf4c9 9828f0fc 3c5ffc68
                     = ba2cf591 7c8fd611 1251a6a6 d282a090 3655e75e 44340b1a 4871a82d cb207b00
          out_digest = 392846e6 3d2af07d 9faa4b77 53f1e1c8 50ed5498 1c819a0b ea142875 4811867b
                     = 9006bd3f 67337d51 380f0d7f c06c4aec 5515067b 9161e066 c0bf7bcd 9a931d79
           explained = yes
          created by   root:
            consumes   file:./
            consumes   file:foo.txt
             creates   file:bar.txt
            supplies   file:bar.txt

file:foo.txt
               state = STATIC
              digest = c178fd84 a7dadcab 4bdb7d96 de15c388 148eda5b 0df7ad9e 7f8e2bf6 8f0e41a8
                     = e40c7bab 7fa20c52 b2a3a769 dbea6989 bc5a53eb 58c946fb daa2af53 4d4b8f33
          created by   root:
            consumes   file:./
            supplies   step:cp foo.txt bar.txt

file:bar.txt
               state = OUTDATED
              digest = 3a189c6b 58667ce2 d87de9df 940fc80c 2f70a873 79bce15c 6f5c8beb 0bac9682
                     = cebfa211 48fd8c69 9442301c 4b96735a bf03fa6d 4d8e0d46 9d089baa 4e67e198
          created by   step:cp foo.txt bar.txt
            consumes   file:./
            consumes   step:cp foo.txt bar.txt
"""


def test_simple_example(wfs: Workflow):
    # Create a runnable step and check the queue
    assert wfs.job_queue.qsize() == 0
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
        assert wfs.get_file_counts() == {FileState.STATIC: 2, FileState.AWAITED: 1}
        assert wfs.get_step_counts() == {StepState.QUEUED: 1}

    # Verify things that should not be allowed
    with pytest.raises(GraphError), wfs.con:
        declare_static(wfs, wfs.root, ["bar.txt"])

    # Simulate the runner, pretending to execute the step
    with wfs.con:
        assert wfs.job_queue.get_nowait().name == "EXECUTE: cp foo.txt bar.txt"
        out_hashes = [("bar.txt", fake_hash("bar.txt"))]
        wfs.update_file_hashes(out_hashes, "succeeded")
        inp_hashes = [("foo.txt", foo.get_hash())]
        env_vars_values = [("A", "B")]
        step_hash = StepHash.from_inp(step.key(), True, inp_hashes, env_vars_values)
        step_hash = step_hash.evolve_out(out_hashes)
        step.completed(step_hash)
        assert wfs.format_str() == TEST_SIMPLE_EXAMPLE_GRAPH3
        assert wfs.get_file_counts() == Counter({FileState.STATIC: 2, FileState.BUILT: 1})
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
    wfs.update_file_hashes([("foo.txt", fake_hash("foo.txt"))], cause="external")
    assert wfs.format_str() == TEST_SIMPLE_EXAMPLE_GRAPH4

    # simulate a skip
    step.completed(step_hash)
    assert wfs.format_str() == TEST_SIMPLE_EXAMPLE_GRAPH3


def test_define_boot_input_static(wfs: Workflow):
    to_check = wfs.define_step(wfs.root, "echo", inp_paths=["foo.txt"])
    assert to_check == []
    echo = wfs.find(Step, "echo")
    declare_static(wfs, wfs.root, ["foo.txt"])
    foo = wfs.find(File, "foo.txt")
    assert echo.creator() is not None
    rootdir = wfs.find(File, "./")
    assert list(foo.consumers()) == [echo]
    assert list(foo.suppliers()) == [rootdir]
    assert list(echo.consumers()) == []
    assert set(echo.suppliers()) == {rootdir, foo}


def test_command_workdir_string(wfs: Workflow):
    with pytest.raises(ValueError):
        wfs.define_step(wfs.root, "echo  # wd=foo/")


def test_define_boot_static_input(wfs: Workflow):
    (foo,) = declare_static(wfs, wfs.root, ["foo.txt"])
    to_check = wfs.define_step(wfs.root, "echo", inp_paths=["foo.txt"])
    assert to_check == []
    echo = wfs.find(Step, "echo")
    assert echo.creator().i is not None
    rootdir = wfs.find(File, "./")
    assert list(foo.consumers()) == [echo]
    assert list(foo.suppliers()) == [rootdir]
    assert list(echo.consumers()) == []
    assert set(echo.suppliers()) == {rootdir, foo}


def test_redefine_boot(wfs: Workflow):
    with wfs.con:
        to_check = wfs.define_step(wfs.root, "echo 1")
        assert to_check == []
        step = wfs.find(Step, "echo 1")
    with pytest.raises(GraphError), wfs.con:
        wfs.define_step(wfs.root, "echo 2")
    with wfs.con:
        step.orphan()
        wfs.define_step(wfs.root, "echo 3")


def test_define_boot_input_orphan(wfs: Workflow):
    wfs.define_step(wfs.root, "echo", inp_paths=["foo.txt"])
    foo = wfs.find(File, "foo.txt")
    assert isinstance(foo, File)
    foo, is_orphan = wfs.find_orphan(File, "foo.txt")
    assert is_orphan
    assert foo.is_orphan()


def test_redefine_step(wfp: Workflow):
    with wfp.con:
        plan = wfp.find(Step, "./plan.py")
        to_check = wfp.define_step(plan, "echo")
        assert to_check == []
        echo = wfp.find(Step, "echo")
        assert echo.get_state() == StepState.QUEUED
        assert list(wfp.nodes(Step)) == [plan, echo]
    with pytest.raises(GraphError), wfp.con:
        wfp.define_step(plan, "echo")
    with wfp.con:
        echo.orphan()
        wfp.define_step(plan, "echo")
        assert echo.get_state() == StepState.QUEUED


def test_define_step_input_static(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    to_check = wfp.define_step(plan, "cat given", inp_paths=["given"])
    assert to_check == []
    cat = wfp.find(Step, "cat given")
    assert cat.get_state() == StepState.PENDING
    declare_static(wfp, plan, ["given"])
    assert cat.get_state() == StepState.QUEUED


def test_define_step_static_input(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    declare_static(wfp, plan, ["given"])
    wfp.define_step(plan, "cat given", inp_paths=["given"])
    cat = wfp.find(Step, "cat given")
    assert cat.get_state() == StepState.QUEUED


def test_define_step_input_pending(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    wfp.define_step(plan, "cat given", inp_paths=["given"])
    cat = wfp.find(Step, "cat given")
    assert cat.get_state() == StepState.PENDING
    file, is_orphan = wfp.find_orphan(File, "given")
    assert is_orphan
    assert file.get_state() == FileState.AWAITED
    wfp.define_step(plan, "touch given", out_paths=["given"])
    touch = wfp.find(Step, "touch given")
    assert touch.get_state() == StepState.QUEUED
    assert file.get_state() == FileState.AWAITED
    wfp.update_file_hashes([("given", fake_hash("given"))], "succeeded")
    touch.completed(StepHash(b"mock", None, b"zzz", None))
    assert touch.get_state() == StepState.SUCCEEDED
    assert file.get_state() == FileState.BUILT
    assert cat.get_state() == StepState.QUEUED


def test_define_step_pending_input(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    wfp.define_step(plan, "touch given", out_paths=["given"])
    touch = wfp.find(Step, "touch given")
    assert touch.get_state() == StepState.QUEUED
    file, is_orphan = wfp.find_orphan(File, "given")
    assert not is_orphan
    assert file.get_state() == FileState.AWAITED
    wfp.define_step(plan, "cat given", inp_paths=["given"])
    cat = wfp.find(Step, "cat given")
    assert cat.get_state() == StepState.PENDING
    assert file.get_state() == FileState.AWAITED
    assert touch.get_state() == StepState.QUEUED
    wfp.update_file_hashes([("given", fake_hash("given"))], "succeeded")
    touch.completed(StepHash(b"mock", None, b"zzz", None))
    assert touch.get_state() == StepState.SUCCEEDED
    assert file.get_state() == FileState.BUILT
    assert cat.get_state() == StepState.QUEUED


def test_define_step_built_input(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    wfp.define_step(plan, "touch given", out_paths=["given"])
    touch = wfp.find(Step, "touch given")
    assert touch.get_state() == StepState.QUEUED
    file, is_orphan = wfp.find_orphan(File, "given")
    assert not is_orphan
    assert file.get_state() == FileState.AWAITED
    wfp.update_file_hashes([("given", fake_hash("given"))], "succeeded")
    touch.completed(StepHash(b"mock", None, b"zzz", None))
    assert touch.get_state() == StepState.SUCCEEDED
    assert file.get_state() == FileState.BUILT
    wfp.define_step(plan, "cat given", inp_paths=["given"])
    cat = wfp.find(Step, "cat given")
    assert cat.get_state() == StepState.QUEUED


def test_define_step_volatile_input(wfp: Workflow):
    with wfp.con:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "touch given", vol_paths=["given"])
        touch = wfp.find(Step, "touch given")
        assert touch.get_state() == StepState.QUEUED
        file, is_orphan = wfp.find_orphan(File, "given")
        assert not is_orphan
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
    file, is_orphan = wfp.find_orphan(File, "given")
    assert is_orphan
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


def test_volatile_directory(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    with pytest.raises(GraphError):
        wfp.define_step(plan, "touch given", vol_paths=["given/"])


def test_define_queued_step_no_pool(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    wfp.define_step(plan, "touch given", vol_paths=["given"])
    step = wfp.find(Step, "touch given")
    assert step.get_state() == StepState.QUEUED
    assert wfp.job_queue.get_nowait().name == "EXECUTE: touch given"


def test_define_queued_step_pool(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    wfp.define_step(plan, "touch given", out_paths=["given"], pool="aa")
    step = wfp.find(Step, "touch given")
    assert step.get_state() == StepState.QUEUED
    assert wfp.job_queue.get_nowait().name == "EXECUTE: touch given"


QUEUED_STEP_SKIP_GRAPH = """\
root:
             creates   file:./
             creates   file:plan.py
             creates   step:./plan.py

file:./
               state = STATIC
          created by   root:
            supplies   file:inp
            supplies   file:out
            supplies   file:plan.py
            supplies   step:./plan.py
            supplies   step:cat < inp > out

file:plan.py
               state = STATIC
              digest = 8fe904f2 160696a3 602d6d9e afff3e1f a6771dfd be57f45a 80f530e7 f0aa16c8
                     = f9767600 18bbe38f 4825c676 9b873603 f41723f7 f31946b8 519a22cd 93210ccb
          created by   root:
            consumes   file:./
            supplies   step:./plan.py

step:./plan.py
               state = RUNNING
          created by   root:
            consumes   file:./
            consumes   file:plan.py
             creates   file:inp
             creates   step:cat < inp > out

file:inp
               state = STATIC
              digest = a85974de 80ede150 82d8e8dd 85de5418 3d5fe2c2 ee2bb31d d4a6ec0f e04aeae5
                     = c0bd8df1 262d0597 0858efa2 ff7722a9 a3e304ae 16526e1c 8a599310 3a6a9a3d
          created by   step:./plan.py
            consumes   file:./
            supplies   step:cat < inp > out

step:cat < inp > out
               state = SUCCEEDED
          inp_digest = 61616161 61616161 61616161 61616161 61616161 61616161 61616161 61616161
                     = 61616161 61616161 61616161 61616161 61616161 61616161 61616161 61616161
          out_digest = 62626262 62626262 62626262 62626262 62626262 62626262 62626262 62626262
                     = 62626262 62626262 62626262 62626262 62626262 62626262 62626262 62626262
          created by   step:./plan.py
            consumes   file:./
            consumes   file:inp
             creates   file:out
            supplies   file:out

file:out
               state = BUILT
              digest = 8b41f2c4 47a3b7f2 68b40473 b925f500 58ca3ff5 95c5cdd8 5de3f0c8 20c14c1f
                     = e42a42fb 27ac8a3d cc91b9f9 143464f8 2b8aa484 9b824085 5661bca4 b4855270
          created by   step:cat < inp > out
            consumes   file:./
            consumes   step:cat < inp > out
"""


def test_define_queued_step_skip(wfp: Workflow):
    # Define workflow
    plan = wfp.find(Step, "./plan.py")
    declare_static(wfp, plan, ["inp"])
    wfp.define_step(plan, "cat < inp > out", inp_paths=["inp"], out_paths=["out"])
    step = wfp.find(Step, "cat < inp > out")

    # Simulate run (first get the plan step and ignore it)
    assert wfp.job_queue.get_nowait().name == "EXECUTE: cat < inp > out"
    wfp.update_file_hashes([("out", fake_hash("out"))], "succeeded")
    step.completed(StepHash(b"a" * 64, None, b"b" * 64, None))

    # Check run
    assert step.get_state() == StepState.SUCCEEDED
    step_hash = step.get_hash()
    assert step_hash.inp_digest == b"a" * 64
    assert step_hash.out_digest == b"b" * 64
    assert wfp.format_str() == QUEUED_STEP_SKIP_GRAPH

    # Simulate input change
    wfp.update_file_hashes([("inp", fake_hash("inp"))], "external")
    assert step.get_state() == StepState.PENDING
    out = wfp.find(File, "out")
    assert out.get_state() == FileState.OUTDATED

    # Simulate rerun
    wfp.queue_pending_steps()
    assert step.get_state() == StepState.QUEUED
    assert wfp.job_queue.get_nowait().name == "SKIP: cat < inp > out"
    step.completed(StepHash(b"a" * 64, None, b"b" * 64, None))
    assert wfp.format_str() == QUEUED_STEP_SKIP_GRAPH
    step.delete_hash()
    assert step.get_hash() is None


def test_define_queued_step_skip_extra(wfp: Workflow):
    # Prepare jobs for normal run
    plan = wfp.find(Step, "./plan.py")
    declare_static(wfp, plan, ["ainp", "ainp2"])
    wfp.job_queue_changed.clear()
    wfp.define_step(plan, "foo > log", env_vars=["VAR"], out_paths=["log"])
    foo = wfp.find(Step, "foo > log")
    assert foo.get_state() == StepState.QUEUED
    assert wfp.job_queue_changed.is_set()
    wfp.define_step(foo, "bar > spam", inp_paths=["log"], env_vars=["X"], vol_paths=["spam"])
    bar = wfp.find(Step, "bar > spam")
    assert bar.get_state() == StepState.PENDING
    plan.completed(StepHash(b"plan_ok", None, b"zzz", None))

    # Simulate run
    # foo
    assert wfp.job_queue.get_nowait().name == "EXECUTE: foo > log"
    keep_going, to_check = wfp.amend_step(
        foo, inp_paths=["ainp"], out_paths=["aout"], vol_paths=["avol"]
    )
    assert keep_going
    assert to_check == []
    wfp.update_file_hashes([("log", fake_hash("log")), ("aout", fake_hash("aout"))], "succeeded")
    foo.completed(StepHash(b"foo_ok", None, b"zzz", None))
    assert foo.get_state() == StepState.SUCCEEDED
    assert bar.get_state() == StepState.QUEUED
    # bar
    bar.mark_pending()  # Should not hurt
    assert bar.get_state() == StepState.QUEUED
    assert wfp.job_queue.get_nowait().name == "EXECUTE: bar > spam"
    wfp.amend_step(bar, inp_paths=["ainp2"], out_paths=["aout2"], vol_paths=["avol2"])
    assert wfp.find(File, "ainp2") in set(bar.suppliers())
    wfp.update_file_hashes([("aout2", fake_hash("aout2"))], "succeeded")
    bar.completed(StepHash(b"bar_ok", None, b"zzz", None))
    assert bar.get_state() == StepState.SUCCEEDED
    txt = wfp.format_str()

    # Make foo pending and check state
    wfp.job_queue_changed.clear()
    foo.mark_pending()
    assert not wfp.job_queue_changed.is_set()
    assert foo.get_hash() is not None
    assert foo.get_state() == StepState.PENDING
    assert not foo.get_validate_amended()
    assert not foo.is_orphan()

    assert wfp.find(File, "log").get_state() == FileState.OUTDATED

    assert bar.get_hash() is not None
    assert bar.get_state() == StepState.PENDING
    assert bar.get_validate_amended()
    spam = wfp.find(File, "spam")
    assert spam is not None
    assert spam.get_state() == FileState.VOLATILE

    # Simulate rerun
    foo.queue_if_appropriate()
    assert wfp.job_queue_changed.is_set()
    assert foo.get_state() == StepState.QUEUED
    assert bar.get_state() == StepState.PENDING
    assert wfp.job_queue.get_nowait().name == "SKIP: foo > log"
    # This simulation assumes that no files have changed and we can just skip foo.
    foo.completed(StepHash(b"foo_ok", None, b"zzz", None))
    assert foo.get_state() == StepState.SUCCEEDED
    assert bar.get_state() == StepState.QUEUED
    assert wfp.job_queue.get_nowait().name == "SKIP: bar > spam"
    # This simulation assumes that no files have changed and we can just skip bar
    bar.completed(StepHash(b"bar_ok", None, b"zzz", None))
    assert bar.get_state() == StepState.SUCCEEDED
    assert wfp.format_str() == txt


def test_skip_step_amended_orphaned_input(wfp: Workflow):
    # Prepare jobs for normal run
    plan = wfp.find(Step, "./plan.py")
    (ainp,) = declare_static(wfp, plan, ["ainp"])
    wfp.job_queue_changed.clear()
    wfp.define_step(plan, "foo > log", out_paths=["log"])
    foo = wfp.find(Step, "foo > log")
    assert foo.get_state() == StepState.QUEUED
    assert list(foo.out_paths()) == ["log"]
    assert wfp.job_queue_changed.is_set()

    # Simulate run
    assert wfp.job_queue.get_nowait().name == "EXECUTE: foo > log"
    wfp.amend_step(foo, inp_paths=["ainp"], out_paths=["aout"], vol_paths=["avol"])
    wfp.update_file_hashes([("log", fake_hash("log")), ("aout", fake_hash("aout"))], "succeeded")
    foo.completed(StepHash(b"foo_ok", None, b"zzz", None))
    assert foo.get_state() == StepState.SUCCEEDED
    txt = wfp.format_str()

    # Make ainp orphan and check state
    wfp.job_queue_changed.clear()
    assert set(foo.out_paths()) == {"aout", "log"}
    ainp.orphan()
    assert set(foo.out_paths()) == {"aout", "log"}
    assert not wfp.job_queue_changed.is_set()
    assert foo.get_hash() is not None
    assert foo.get_state() == StepState.SUCCEEDED
    assert wfp.find(File, "log").get_state() == FileState.BUILT

    # not even skip
    declare_static(wfp, plan, ["ainp"])
    assert foo.get_hash() is not None
    assert foo.get_state() == StepState.SUCCEEDED
    log = wfp.find(File, "log")
    assert log.get_state() == FileState.BUILT
    assert wfp.format_str() == txt


def test_skip_ngm(wfp: Workflow):
    # Prepare jobs for normal run
    plan = wfp.find(Step, "./plan.py")
    wfp.job_queue_changed.clear()
    wfp.define_step(plan, "foo")
    foo = wfp.find(Step, "foo")
    assert foo.get_state() == StepState.QUEUED
    assert wfp.job_queue_changed.is_set()
    plan.completed(StepHash(b"plan_ok", None, b"ee", None))
    assert plan.get_state() == StepState.SUCCEEDED

    # Simulate run
    assert wfp.job_queue.get_nowait().name == "EXECUTE: foo"
    ngm = NGlobMulti.from_patterns(["${*prefix}_data.txt"], subs={"prefix": "n???"})
    wfp.register_nglob(foo, ngm)
    foo.completed(StepHash(b"foo_ok", None, b"zzz", None))
    assert foo.get_hash() is not None
    assert foo.get_state() == StepState.SUCCEEDED

    # Make foo pending and check state
    wfp.job_queue_changed.clear()
    foo.mark_pending()
    assert not wfp.job_queue_changed.is_set()
    assert foo.get_hash() is not None
    assert foo.get_state() == StepState.PENDING

    # Skip
    foo.queue_if_appropriate()
    assert wfp.job_queue_changed.is_set()
    assert foo.get_state() == StepState.QUEUED
    assert wfp.job_queue.get_nowait().name == "SKIP: foo"
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
    step_hash = StepHash(b"p" * 64, None, b"p" * 64, None)
    plan.completed(step_hash)
    assert step_hash == plan.get_hash()


def test_amend_step(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    wfp.define_step(plan, "blub > log", vol_paths=["log"])
    step = wfp.find(Step, "blub > log")
    assert step.get_state() == StepState.QUEUED
    assert wfp.job_queue.get_nowait().name == "EXECUTE: blub > log"
    assert wfp.amend_step(step)
    keep_going, to_check = wfp.amend_step(
        step, inp_paths=["inp1", "inp2"], out_paths=["out3"], vol_paths=["vol4"]
    )
    assert not keep_going
    assert to_check == []
    assert set(step.inp_paths(yield_orphan=True, amended=True)) == {("inp1", True), ("inp2", True)}
    assert set(step.out_paths(amended=True)) == {"out3"}
    assert set(step.vol_paths(amended=True)) == {"vol4"}
    step.completed(None)
    step.set_state(StepState.PENDING)
    declare_static(wfp, plan, ["inp1"])
    step.set_state(StepState.PENDING)
    declare_static(wfp, plan, ["inp2"])
    step.set_state(StepState.QUEUED)
    assert [node.key() for node in step.products()] == ["file:log", "file:out3", "file:vol4"]
    assert wfp.job_queue.get_nowait().name == "EXECUTE: blub > log"


QUEUED_STEP_SKIP_AMENDED_GRAPH = """\
root:
             creates   file:./
             creates   file:plan.py
             creates   step:./plan.py

file:./
               state = STATIC
          created by   root:
            supplies   file:ainp
            supplies   file:aout
            supplies   file:avol
            supplies   file:inp
            supplies   file:out
            supplies   file:plan.py
            supplies   file:vol
            supplies   step:./plan.py
            supplies   step:cat < inp > out 2> vol

file:plan.py
               state = STATIC
              digest = 8fe904f2 160696a3 602d6d9e afff3e1f a6771dfd be57f45a 80f530e7 f0aa16c8
                     = f9767600 18bbe38f 4825c676 9b873603 f41723f7 f31946b8 519a22cd 93210ccb
          created by   root:
            consumes   file:./
            supplies   step:./plan.py

step:./plan.py
               state = RUNNING
          created by   root:
            consumes   file:./
            consumes   file:plan.py
             creates   file:ainp
             creates   file:inp
             creates   step:cat < inp > out 2> vol

file:ainp
               state = STATIC
              digest = 3ea8f987 aee16bf8 d949e94b 836a3f77 591716b2 20ecd9c4 2c86835b 1016be99
                     = 5c49eb3d 94bbd998 895f5732 72ca40f3 91defae1 91935475 645aaace cf0eb27c
          created by   step:./plan.py
            consumes   file:./
            supplies   step:cat < inp > out 2> vol

file:inp
               state = STATIC
              digest = a85974de 80ede150 82d8e8dd 85de5418 3d5fe2c2 ee2bb31d d4a6ec0f e04aeae5
                     = c0bd8df1 262d0597 0858efa2 ff7722a9 a3e304ae 16526e1c 8a599310 3a6a9a3d
          created by   step:./plan.py
            consumes   file:./
            supplies   step:cat < inp > out 2> vol

step:cat < inp > out 2> vol
               state = SUCCEEDED
          inp_digest = 63636363 63636363 63636363 63636363 63636363 63636363 63636363 63636363
                     = 63636363 63636363 63636363 63636363 63636363 63636363 63636363 63636363
          out_digest = 64646464 64646464 64646464 64646464 64646464 64646464 64646464 64646464
                     = 64646464 64646464 64646464 64646464 64646464 64646464 64646464 64646464
          created by   step:./plan.py
            consumes   file:./
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
              digest = 8b41f2c4 47a3b7f2 68b40473 b925f500 58ca3ff5 95c5cdd8 5de3f0c8 20c14c1f
                     = e42a42fb 27ac8a3d cc91b9f9 143464f8 2b8aa484 9b824085 5661bca4 b4855270
          created by   step:cat < inp > out 2> vol
            consumes   file:./
            consumes   step:cat < inp > out 2> vol

file:vol
               state = VOLATILE
          created by   step:cat < inp > out 2> vol
            consumes   file:./
            consumes   step:cat < inp > out 2> vol

file:aout
               state = BUILT
              digest = e51e3778 cc6a670f cb015786 3a4879f9 c5dd6046 5883f2bd 40d9a4e3 95dd24b9
                     = b57deea3 218b12fb 70b05c8f 2ecaa69c 6bcbc54a 687c49a8 ebf9c708 587397cf
          created by   step:cat < inp > out 2> vol
            consumes   file:./
            consumes   step:cat < inp > out 2> vol

file:avol
               state = VOLATILE
          created by   step:cat < inp > out 2> vol
            consumes   file:./
            consumes   step:cat < inp > out 2> vol
"""


def test_define_queued_step_skip_amended(wfp: Workflow):
    # Define workflow
    plan = wfp.find(Step, "./plan.py")
    declare_static(wfp, plan, ["inp", "ainp"])
    wfp.define_step(
        plan, "cat < inp > out 2> vol", inp_paths=["inp"], out_paths=["out"], vol_paths=["vol"]
    )
    step = wfp.find(Step, "cat < inp > out 2> vol")

    # Simulate run (first get the plan step and ignore it)
    assert wfp.job_queue.get_nowait().name == "EXECUTE: cat < inp > out 2> vol"
    wfp.amend_step(step, inp_paths=["ainp"], out_paths=["aout"], vol_paths=["avol"])
    wfp.update_file_hashes([("out", fake_hash("out")), ("aout", fake_hash("aout"))], "succeeded")
    step.completed(StepHash(b"c" * 64, None, b"d" * 64, None))
    assert wfp.format_str() == QUEUED_STEP_SKIP_AMENDED_GRAPH

    # Check run
    assert step.get_state() == StepState.SUCCEEDED
    step_hash = step.get_hash()
    assert step_hash.inp_digest == b"c" * 64
    assert step_hash.out_digest == b"d" * 64

    # Simulate amended input change
    wfp.update_file_hashes([("ainp", fake_hash("ainp"))], "external")
    assert step.get_state() == StepState.PENDING
    out = wfp.find(File, "out")
    assert out.get_state() == FileState.OUTDATED

    # Simulate and check rerun
    wfp.queue_pending_steps()
    assert step.get_state() == StepState.QUEUED
    assert wfp.job_queue.get_nowait().name == "SKIP: cat < inp > out 2> vol"
    step.completed(StepHash(b"c" * 64, None, b"d" * 64, None))
    assert {node.key() for node in step.suppliers(include_orphans=True)} == {
        "file:./",
        "file:ainp",
        "file:inp",
    }
    assert isinstance(wfp.find(File, "aout"), File)
    assert isinstance(wfp.find(File, "avol"), File)
    assert {node.key() for node in step.suppliers(include_orphans=True)} == {
        "file:./",
        "file:inp",
        "file:ainp",
    }
    assert wfp.find(File, "aout").creator() == step
    assert wfp.find(File, "avol").creator() == step
    assert wfp.format_str() == QUEUED_STEP_SKIP_AMENDED_GRAPH

    # Check deleteion of hash
    step.delete_hash()
    assert step.get_hash() is None


REGISTER_NGLOB_GRAPH = """\
root:
             creates   file:./
             creates   file:plan.py
             creates   step:./plan.py

file:./
               state = STATIC
          created by   root:
            supplies   file:log
            supplies   file:plan.py
            supplies   step:./plan.py
            supplies   step:touch log

file:plan.py
               state = STATIC
              digest = 8fe904f2 160696a3 602d6d9e afff3e1f a6771dfd be57f45a 80f530e7 f0aa16c8
                     = f9767600 18bbe38f 4825c676 9b873603 f41723f7 f31946b8 519a22cd 93210ccb
          created by   root:
            consumes   file:./
            supplies   step:./plan.py

step:./plan.py
               state = RUNNING
          created by   root:
            consumes   file:./
            consumes   file:plan.py
             creates   step:touch log

step:touch log
               state = QUEUED
                 ngm = ['*.txt'] {}
          created by   step:./plan.py
            consumes   file:./
             creates   file:log
            supplies   file:log

file:log
               state = VOLATILE
          created by   step:touch log
            consumes   file:./
            consumes   step:touch log
"""


def test_register_nglob(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    wfp.define_step(plan, "touch log", vol_paths=["log"])
    step = wfp.find(Step, "touch log")
    ngm = NGlobMulti.from_patterns(["*.txt"])
    wfp.register_nglob(step, ngm)
    assert list(wfp.steps(StepState.RUNNING)) == [plan]
    assert list(wfp.steps(StepState.QUEUED)) == [step]
    assert list(step.nglob_multis()) == [ngm]
    assert list(wfp.nglob_multis(yield_step=True)) == [(1, ngm, step)]
    assert wfp.format_str() == REGISTER_NGLOB_GRAPH

    # Orphaning does not clear products
    step.orphan()
    assert list(step.nglob_multis()) == [ngm]
    assert list(wfp.nglob_multis(yield_step=True)) == [(1, ngm, step)]
    wfp.clean()
    assert list(step.nglob_multis()) == []
    assert list(wfp.nglob_multis(yield_step=True)) == []


def test_is_relevant(wfp: Workflow):
    assert wfp.is_relevant("plan.py")
    assert wfp.is_relevant("./")
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
        assert work.get_state() == StepState.QUEUED
        wfp.update_file_hashes([("aa1_bar.txt", fake_hash("ok"))], "succeeded")
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
            "external",
        )
        wfp.process_nglob_changes({"cc5_foo.txt"}, {"bb7_bar.txt"})
        wfp.queue_pending_steps()

    # The top-level plan became pending (and queued again), so the step work becomes orphan.
    assert plan.get_state() == StepState.QUEUED
    assert work.get_state() == StepState.PENDING
    assert not work.is_orphan()
    assert not aa1_bar.is_orphan()
    assert aa1_bar.get_state() == FileState.AWAITED
    assert cc5_foo is not None
    assert cc5_foo.get_state() == FileState.MISSING
    assert wfp.find(File, "bb7_bar.txt") is None
    ngm = next(plan.nglob_multis())
    assert ngm.files() == ("aa1_bar.txt", "aa1_foo.txt", "bb7_bar.txt", "bb7_foo.txt")
    assert ngm.nglob_singles[0].results == {("aa1",): {"aa1_foo.txt"}, ("bb7",): {"bb7_foo.txt"}}
    assert ngm.nglob_singles[1].results == {("aa1",): {"aa1_bar.txt"}, ("bb7",): {"bb7_bar.txt"}}


def test_externally_updated_static_orphan(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    declare_static(wfp, plan, ["foo.txt"])
    foo = wfp.find(File, "foo.txt")
    foo.orphan()
    foo.set_state(FileState.MISSING)
    wfp.update_file_hashes([("foo.txt", fake_hash("foo.txt"))], "external")
    assert foo.is_orphan()
    assert foo.get_state() == FileState.STATIC


def test_externally_updated_static_missing(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    declare_static(wfp, plan, ["foo.txt"])
    foo = wfp.find(File, "foo.txt")
    foo.set_state(FileState.MISSING)
    wfp.update_file_hashes([("foo.txt", fake_hash("foo.txt"))], "external")
    assert foo.creator().i == plan.i
    assert foo.get_state() == FileState.STATIC


def test_externally_deleted_static_orphan(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    declare_static(wfp, plan, ["foo.txt"])
    foo = wfp.find(File, "foo.txt")
    foo.orphan()
    wfp.update_file_hashes([("foo.txt", FileHash.unknown())], "external")
    assert foo.is_orphan()
    assert foo.get_state() == FileState.MISSING
    assert foo.get_hash() == FileHash.unknown()


def test_externally_updated_built_orphan(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    wfp.define_step(plan, "touch foo.txt", out_paths=["foo.txt"])
    step = wfp.find(Step, "touch foo.txt")
    step.orphan()
    assert step.get_state() == StepState.QUEUED
    with pytest.raises(AssertionError):
        wfp.update_file_hashes([("foo.txt", fake_hash("foo.txt"))], "external")
    assert step.get_state() == StepState.QUEUED


def test_externally_deleted_built_orphan(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    wfp.define_step(plan, "touch foo.txt", out_paths=["foo.txt"])
    step = wfp.find(Step, "touch foo.txt")
    step.orphan()
    assert step.get_state() == StepState.QUEUED
    with pytest.raises(AssertionError):
        wfp.update_file_hashes([("foo.txt", FileHash.unknown())], "external")
    assert step.get_state() == StepState.QUEUED


def test_directory_usage(wfs: Workflow):
    assert wfs.dir_queue.get_nowait() == (DirWatch.START, "./")
    assert wfs.dir_queue.empty()
    declare_static(wfs, wfs.root, ["foo.txt"])
    assert wfs.dir_queue.empty()
    declare_static(wfs, wfs.root, ["sub/", "sub/bar.txt"])
    assert wfs.dir_queue.get_nowait() == (DirWatch.START, "sub/")
    assert wfs.dir_queue.empty()
    for path in "sub/", "sub/bar.txt", "foo.txt", "./":
        wfs.find(File, path).orphan()
        assert wfs.dir_queue.empty()
    wfs.clean()
    assert wfs.dir_queue.get_nowait() == (DirWatch.STOP, "sub/")
    assert wfs.dir_queue.get_nowait() == (DirWatch.STOP, "./")
    assert wfs.dir_queue.empty()


def test_parent_stays_alive(wfp: Workflow):
    # When a parent directory is orphaned,
    # it cannot be cleaned until all files or subdirectories are orphaned.
    plan = wfp.find(Step, "./plan.py")
    [sub, foo] = declare_static(wfp, plan, ["sub/", "sub/foo"])
    sub.orphan()
    assert sub.is_orphan()
    wfp.clean()
    assert sub.is_orphan()
    foo.orphan()
    assert foo.is_orphan()
    wfp.clean()
    assert wfp.find_orphan(File, "sub/") == (None, None)
    assert wfp.find_orphan(File, "sub/foo") == (None, None)


def test_to_be_deleted(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    declare_static(wfp, plan, ["static"])
    wfp.define_step(plan, "blub1", out_paths=["built", "gone"])
    blub1 = wfp.find(Step, "blub1")
    wfp.define_step(plan, "blub2", vol_paths=["volatile"])
    wfp.define_step(plan, "blub3", out_paths=["pending"])
    wfp.define_step(plan, "mkdir sub", out_paths=["sub/"])
    built_file_hash = fake_hash("built")
    gone_file_hash = fake_hash("mockg")
    sub_file_hash = fake_hash("sub/")
    wfp.update_file_hashes(
        [("built", built_file_hash), ("gone", gone_file_hash), ("sub/", sub_file_hash)], "succeeded"
    )
    blub1.completed(StepHash(b"aaa", None, b"zzz", None))
    plan.orphan()
    assert wfp.to_be_deleted == []
    assert wfp.find_orphan(Step, "./plan.py") == (plan, True)
    wfp.clean()
    assert wfp.to_be_deleted == [
        ("built", built_file_hash),
        ("gone", gone_file_hash),
        ("volatile", None),
        ("sub/", sub_file_hash),
    ]
    assert wfp.find_orphan(Step, "./plan.py") == (None, None)


def test_externally_deleted(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    (tst,) = declare_static(wfp, wfp.root, ["tst"])
    wfp.define_step(plan, "bla1", out_paths=["prr"])
    step1 = wfp.find(Step, "bla1")
    wfp.define_step(plan, "bla2", inp_paths=["prr"])
    step2 = wfp.find(Step, "bla2")

    # Static
    wfp.update_file_hashes([("tst", FileHash.unknown())], "external")
    assert tst.get_state() == FileState.MISSING
    with pytest.raises(AssertionError):
        wfp.update_file_hashes([("tst", FileHash.unknown())], "external")

    # Built
    prr = wfp.find(File, "prr")
    assert prr.get_state() == FileState.AWAITED
    with pytest.raises(AssertionError):
        wfp.update_file_hashes([("prr", FileHash.unknown())], "external")
    assert prr.get_state() == FileState.AWAITED
    wfp.update_file_hashes([("prr", fake_hash("prr"))], "succeeded")
    step1.completed(StepHash(b"11", None, b"zzz", None))
    step2.completed(None)
    assert prr.get_state() == FileState.BUILT
    wfp.update_file_hashes([("prr", FileHash.unknown())], "external")
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
    wfp.update_file_hashes([("tst", FileHash.unknown())], "external")
    assert tst.get_state() == FileState.MISSING
    assert cat.get_state() == StepState.PENDING
    wfp.update_file_hashes([("tst", fake_hash("tst"))], "external")
    assert tst.get_state() == FileState.STATIC
    assert cat.get_state() == StepState.PENDING

    # Built
    prr = wfp.find(File, "prr")
    assert prr.get_state() == FileState.AWAITED
    wfp.update_file_hashes([("prr", fake_hash("prr"))], "succeeded")
    step1.completed(StepHash(b"11", None, b"zzz", None))
    step2.completed(None)
    assert prr.get_state() == FileState.BUILT
    assert step2.get_state() == StepState.FAILED
    wfp.update_file_hashes([("prr", FileHash.unknown())], "external")
    assert prr.get_state() == FileState.AWAITED
    assert step1.get_state() == StepState.PENDING
    assert step2.get_state() == StepState.PENDING


def test_step_recycle(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    wfp.define_step(plan, "echo foo > bar", out_paths=["bar"])
    echo = wfp.find(Step, "echo foo > bar")
    step_hash = StepHash(b"bsfssfdsdfsdfasdfasa", None, b"zzz", None)
    wfp.update_file_hashes([("bar", fake_hash("bar"))], "succeeded")
    echo.completed(step_hash)
    hash1 = echo.get_hash()
    assert hash1 is not None

    # Orphan and recycle
    echo.orphan()
    wfp.define_step(plan, "echo foo > bar", out_paths=["bar"])
    hash2 = echo.get_hash()
    assert hash2 is not None
    assert hash1.inp_digest == hash2.inp_digest
    assert hash1.out_digest == hash2.out_digest


def test_output_dir_nested(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    wfp.define_step(plan, "mkdir -p s/foo/bar/egg", out_paths=["s/foo/bar/egg/"])
    step = wfp.find(Step, "mkdir -p s/foo/bar/egg")
    wfp.clean()
    f, is_orphan = wfp.find_orphan(File, "s/")
    assert isinstance(f, File)
    assert is_orphan
    f, is_orphan = wfp.find_orphan(File, "s/foo/")
    assert isinstance(f, File)
    assert is_orphan
    f, is_orphan = wfp.find_orphan(File, "s/foo/bar/")
    assert isinstance(f, File)
    assert is_orphan
    f, is_orphan = wfp.find_orphan(File, "s/foo/bar/egg/")
    assert isinstance(f, File)
    assert not is_orphan
    assert f.creator().i == step.i

    step.orphan()
    f, is_orphan = wfp.find_orphan(File, "s/")
    assert isinstance(f, File)
    assert is_orphan
    f, is_orphan = wfp.find_orphan(File, "s/foo/bar/egg/")
    assert isinstance(f, File)
    assert is_orphan

    wfp.clean()
    assert wfp.find_orphan(File, "s/") == (None, None)
    assert wfp.find_orphan(File, "s/foo/") == (None, None)
    assert wfp.find_orphan(File, "s/foo/bar/") == (None, None)
    assert wfp.find_orphan(File, "s/foo/bar/egg/") == (None, None)


def test_clean_multiple_suppliers(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    (file,) = declare_static(wfp, plan, ["common.txt"])
    wfp.define_step(plan, "prog1 common.txt", inp_paths=["common.txt"], out_paths=["output1.txt"])
    step1 = wfp.find(Step, "prog1 common.txt")
    wfp.define_step(plan, "prog2 common.txt", inp_paths=["common.txt"], out_paths=["output2.txt"])
    step2 = wfp.find(Step, "prog2 common.txt")
    file.orphan()
    wfp.clean()
    assert file.is_orphan()
    step1.orphan()
    wfp.clean()
    assert file.is_orphan()
    step2.orphan()
    wfp.clean()
    assert wfp.find_orphan(File, "common.txt") == (None, None)


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
    assert step.get_state() == StepState.QUEUED
    assert wfp.job_queue.get_nowait().name == "EXECUTE: prog1"
    wfp.amend_step(step, env_vars=["foo", "egg"])
    wfp.amend_step(step, env_vars=["foo", "bar"])
    assert set(step.env_vars(amended=False)) == {"egg"}
    assert set(step.env_vars(amended=True)) == {"bar", "foo"}
    assert set(step.env_vars()) == {"bar", "egg", "foo"}


def test_acyclic_amend_static(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    assert plan.get_state() == StepState.RUNNING
    declare_static(wfp, plan, ["static.txt"])
    wfp.amend_step(plan, inp_paths=["static.txt"])
    assert set(plan.inp_paths()) == {"./", "plan.py", "static.txt"}
    assert set(plan.static_paths()) == {"static.txt"}


def test_cyclic_two_steps(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    wfp.define_step(plan, "cat first > second", inp_paths=["first"], out_paths=["second"])
    with pytest.raises(GraphError):
        wfp.define_step(plan, "cat second > first", inp_paths=["second"], out_paths=["first"])


def test_optional_imply(wfp: Workflow):
    # Define sequence of steps: optional -> mandatory
    plan = wfp.find(Step, "./plan.py")
    wfp.define_step(plan, "prog1", out_paths=["foo"], optional=True)
    step1 = wfp.find(Step, "prog1")
    assert step1.get_mandatory() == Mandatory.NO
    assert step1.get_state() == StepState.PENDING
    wfp.define_step(plan, "prog2", inp_paths=["foo"], out_paths=["bar"])
    step2 = wfp.find(Step, "prog2")
    assert step2.get_mandatory() == Mandatory.YES
    assert step2.get_state() == StepState.PENDING
    assert step1.get_mandatory() == Mandatory.REQUIRED
    assert step1.get_state() == StepState.QUEUED

    # Simulate scheduler
    plan.completed(StepHash(b"p" * 64, None, b"p" * 64, None))
    assert wfp.job_queue.get_nowait().name == "EXECUTE: prog1"
    assert wfp.job_queue.qsize() == 0
    wfp.update_file_hashes([("foo", fake_hash("foo"))], "succeeded")
    step1.completed(StepHash(b"1" * 64, None, b"1" * 64, None))
    assert step2.get_state() == StepState.QUEUED
    assert wfp.job_queue.get_nowait().name == "EXECUTE: prog2"
    wfp.update_file_hashes([("bar", fake_hash("bar"))], "succeeded")
    step2.completed(StepHash(b"2" * 64, None, b"2" * 64, None))
    assert step2.get_state() == StepState.SUCCEEDED

    # Simulate rerunning with new plan from which step 2 is removed:
    # - orphan step 2
    step2.orphan()
    assert step2.is_orphan()
    assert step1.get_mandatory() == Mandatory.NO
    assert step1.get_state() == StepState.SUCCEEDED
    foo, is_orphan = wfp.find_orphan(File, "foo")
    assert not is_orphan
    assert foo.get_state() == FileState.BUILT
    bar, is_orphan = wfp.find_orphan(File, "bar")
    assert is_orphan
    assert bar.get_state() == FileState.BUILT

    # - run clean
    wfp.clean()
    foo, is_orphan = wfp.find_orphan(File, "foo")
    assert not is_orphan
    assert foo.get_state() == FileState.OUTDATED
    assert len(wfp.to_be_deleted) == 2
    assert wfp.to_be_deleted[0][0] == "foo"
    assert wfp.to_be_deleted[1][0] == "bar"


def test_optional_imply_chain(wfp: Workflow):
    # Define sequence of steps: optional -> optional -> mandatory
    plan = wfp.find(Step, "./plan.py")
    wfp.define_step(plan, "prog1", out_paths=["foo"], optional=True)
    step1 = wfp.find(Step, "prog1")
    assert step1.get_mandatory() == Mandatory.NO
    assert step1.get_state() == StepState.PENDING
    wfp.define_step(plan, "prog2", inp_paths=["foo"], out_paths=["bar"], optional=True)
    step2 = wfp.find(Step, "prog2")
    assert step2.get_mandatory() == Mandatory.NO
    assert step2.get_state() == StepState.PENDING
    wfp.define_step(plan, "prog3", inp_paths=["bar"])
    step3 = wfp.find(Step, "prog3")
    assert step3.get_mandatory() == Mandatory.YES
    assert step3.get_state() == StepState.PENDING
    assert step2.get_mandatory() == Mandatory.REQUIRED
    assert step2.get_state() == StepState.PENDING
    assert step1.get_mandatory() == Mandatory.REQUIRED
    assert step1.get_state() == StepState.QUEUED

    # Simulate scheduler
    assert wfp.job_queue.get_nowait().name == "EXECUTE: prog1"
    assert wfp.job_queue.qsize() == 0
    wfp.update_file_hashes([("foo", fake_hash("foo"))], "succeeded")
    step1.completed(StepHash(b"sth", None, b"zzz", None))
    assert step2.get_state() == StepState.QUEUED
    assert wfp.job_queue.get_nowait().name == "EXECUTE: prog2"
    wfp.update_file_hashes([("bar", fake_hash("bar"))], "succeeded")
    step2.completed(StepHash(b"sth", None, b"zzz", None))
    assert step3.get_state() == StepState.QUEUED
    assert wfp.job_queue.get_nowait().name == "EXECUTE: prog3"
    step3.completed(StepHash(b"sth", None, b"zzz", None))
    assert step3.get_state() == StepState.SUCCEEDED

    # Simulate watcher: orphan middle step
    step2.orphan()
    assert step1.get_mandatory() == Mandatory.NO
    assert step1.get_state() == StepState.SUCCEEDED
    assert step2.get_mandatory() == Mandatory.NO
    assert step2.get_state() == StepState.SUCCEEDED
    assert step3.get_mandatory() == Mandatory.YES
    assert step3.get_state() == StepState.SUCCEEDED


def test_optional_infer(wfp: Workflow):
    # Define sequence of steps: optional -> mandatory
    plan = wfp.find(Step, "./plan.py")
    wfp.define_step(plan, "prog1", inp_paths=["foo"])
    step1 = wfp.find(Step, "prog1")
    assert step1.get_mandatory() == Mandatory.YES
    assert step1.get_state() == StepState.PENDING
    wfp.define_step(plan, "prog2", out_paths=["foo"], optional=True)
    step2 = wfp.find(Step, "prog2")
    assert step2.get_mandatory() == Mandatory.REQUIRED
    assert step2.get_state() == StepState.QUEUED


def test_optional_infer_chained(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    wfp.define_step(plan, "prog1", inp_paths=["foo"])
    step1 = wfp.find(Step, "prog1")
    assert step1.get_mandatory() == Mandatory.YES
    assert step1.get_state() == StepState.PENDING
    wfp.define_step(plan, "prog2", out_paths=["bar"], optional=True)
    step2 = wfp.find(Step, "prog2")
    assert step2.get_mandatory() == Mandatory.NO
    assert step2.get_state() == StepState.PENDING
    wfp.define_step(plan, "prog3", inp_paths=["bar"], out_paths=["foo"], optional=True)
    step3 = wfp.find(Step, "prog3")
    assert step3.get_mandatory() == Mandatory.REQUIRED
    assert step3.get_state() == StepState.PENDING
    assert step2.get_mandatory() == Mandatory.REQUIRED
    assert step2.get_state() == StepState.QUEUED
    assert step1.get_mandatory() == Mandatory.YES
    assert step1.get_state() == StepState.PENDING


def test_deferred_glob_basic(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    # Define a step with an orphan input
    to_check = wfp.define_step(plan, "cat head1.txt", inp_paths=["head1.txt"])
    assert to_check == []

    # Define deferred glob and check attributes
    with pytest.raises(ValueError):
        wfp.defer_glob(plan, "head${*char}.txt")
    to_check_h = wfp.defer_glob(plan, "head*.txt")
    to_check_t = wfp.defer_glob(plan, "tail*.txt")
    assert isinstance(wfp.find(DeferredGlob, "head*.txt"), DeferredGlob)
    assert isinstance(wfp.find(DeferredGlob, "tail*.txt"), DeferredGlob)

    # Validate the to_check result
    assert to_check_h == [("head1.txt", FileHash.unknown())]
    assert to_check_t == []
    head1 = wfp.find(File, "head1.txt")
    assert head1.get_state() == FileState.MISSING

    # Check if head_1.txt is static after confirming
    wfp.update_file_hashes([("head1.txt", fake_hash("head1.txt"))], "confirmed")
    assert head1.get_state() == FileState.STATIC

    # Use deferred glob after it is added
    to_check = wfp.define_step(plan, "cat tail1.txt", inp_paths=["tail1.txt"])
    assert to_check == [("tail1.txt", FileHash.unknown())]
    tail1 = wfp.find(File, "tail1.txt")
    assert tail1.get_state() == FileState.MISSING
    with pytest.raises(AssertionError):
        wfp.update_file_hashes(to_check, "confirmed")
    wfp.update_file_hashes([("tail1.txt", fake_hash("tail1.txt"))], "confirmed")
    assert tail1.get_state() == FileState.STATIC


def test_deferred_glob_clean(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    to_check = wfp.defer_glob(plan, "static/**")
    assert len(to_check) == 0
    inp_paths = ["static/foo/bar.txt"]
    to_check = wfp.define_step(plan, "cat static/foo/bar.txt", inp_paths=inp_paths)
    assert to_check == [
        ("static/", FileHash.unknown()),
        ("static/foo/", FileHash.unknown()),
        ("static/foo/bar.txt", FileHash.unknown()),
    ]
    wfp.update_file_hashes(
        [
            ("static/", fake_hash("static/")),
            ("static/foo/", fake_hash("static/foo/")),
            ("static/foo/bar.txt", fake_hash("static/foo/bar.txt")),
        ],
        "confirmed",
    )
    step = wfp.find(Step, "cat static/foo/bar.txt")

    # Check effect of defining the step on the deferred_glob
    dg = wfp.find(DeferredGlob, "static/**")
    assert wfp.find(File, "static/").get_state() == FileState.STATIC
    assert wfp.find(File, "static/foo/").get_state() == FileState.STATIC
    assert wfp.find(File, "static/foo/bar.txt").get_state() == FileState.STATIC

    # Simulate the execution of the steps
    plan.completed(StepHash(b"sthp", None, b"zzz", None))
    assert wfp.job_queue.get_nowait().name == "EXECUTE: cat static/foo/bar.txt"
    assert wfp.job_queue.qsize() == 0
    step.completed(StepHash(b"sths", None, b"zzz", None))

    # Check the hashes
    assert plan.get_hash().inp_digest == b"sthp"
    assert step.get_hash().inp_digest == b"sths"

    # Orphan the step, manually outdate it, clean and check result
    step.orphan()
    wfp.clean()
    assert dg.creator().i == plan.i
    assert not step.is_alive()
    assert wfp.find_orphan(File, "static/") == (None, None)
    assert wfp.find_orphan(File, "static/foo/") == (None, None)
    assert wfp.find_orphan(File, "static/foo/bar.txt") == (None, None)

    # make the plan pending and check if it can be queued
    plan.mark_pending()
    assert not dg.is_orphan()
    plan.queue_if_appropriate()
    assert plan.get_state() == StepState.QUEUED
    assert wfp.job_queue.qsize() == 1


def test_deferred_glob_two_matches(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    wfp.defer_glob(plan, "*.md")
    wfp.defer_glob(plan, "README.*")
    with pytest.raises(GraphError):
        wfp.define_step(plan, "cat README.md", inp_paths=["README.md"])


def test_deferred_glob_static(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    wfp.defer_glob(plan, "*.md")
    with pytest.raises(GraphError):
        declare_static(wfp, plan, ["README.md"])


def test_deferred_glob_output(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    wfp.defer_glob(plan, "*.md")
    with pytest.raises(GraphError):
        wfp.define_step(plan, "echo foo > README.md", out_paths=["README.md"])


def test_deferred_glob_volatile(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    wfp.defer_glob(plan, "*.md")
    with pytest.raises(GraphError):
        wfp.define_step(plan, "echo foo > README.md", vol_paths=["README.md"])


def test_orhphaned_deferred_glob(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    wfp.defer_glob(plan, "*.md")
    wfp.defer_glob(plan, "*.txt")
    wfp.find(DeferredGlob, "*.txt").orphan()
    to_check = wfp.define_step(plan, "prog", inp_paths=["README.md", "README.txt"])
    assert to_check == [("README.md", FileHash.unknown())]


def test_deferred_glob_amend_inp(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    wfp.defer_glob(plan, "*.md")
    to_check = wfp.define_step(plan, "prog", inp_paths=["initial.md", "initial.txt"])
    assert to_check == [("initial.md", FileHash.unknown())]
    prog = wfp.find(Step, "prog")
    keep_going, to_check = wfp.amend_step(prog, inp_paths=["other.md"])
    assert keep_going
    assert to_check == [("other.md", FileHash.unknown())]
    keep_going, to_check = wfp.amend_step(prog, inp_paths=["amended.md", "amended.txt"])
    assert not keep_going
    assert to_check == [("amended.md", FileHash.unknown())]


def test_deferred_glob_amend_out(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    wfp.defer_glob(plan, "data*/")
    to_check = wfp.define_step(
        plan, "prog", out_paths=["data_out_initial/out.txt"], vol_paths=["data_vol_initial/vol.txt"]
    )
    assert to_check == [
        ("data_out_initial/", FileHash.unknown()),
        ("data_vol_initial/", FileHash.unknown()),
    ]
    prog = wfp.find(Step, "prog")
    keep_going, to_check = wfp.amend_step(
        prog, out_paths=["data_out_amended/out.txt"], vol_paths=["data_vol_amended/vol.txt"]
    )
    assert keep_going
    assert to_check == [
        ("data_out_amended/", FileHash.unknown()),
        ("data_vol_amended/", FileHash.unknown()),
    ]


def test_deferred_glob_recursive_dirs(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    wfp.defer_glob(plan, "data/**/")
    to_check = wfp.declare_missing(plan, ["data/foo/a/bar.txt", "data/foo/b/egg.txt"])
    assert to_check == [
        ("data/", FileHash.unknown()),
        ("data/foo/", FileHash.unknown()),
        ("data/foo/a/", FileHash.unknown()),
        ("data/foo/a/bar.txt", FileHash.unknown()),
        ("data/foo/b/", FileHash.unknown()),
        ("data/foo/b/egg.txt", FileHash.unknown()),
    ]


def test_define_step_reqdir_out_path(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    wfp.define_step(plan, "echo", out_paths=["sub/dir/out"])
    reqdir, is_orphan = wfp.find_orphan(File, "sub/dir/")
    assert is_orphan
    assert reqdir.get_state() == FileState.AWAITED


def test_define_step_reqdir_vol_path(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    wfp.define_step(plan, "echo", vol_paths=["sub/dir/vol"])
    reqdir, is_orphan = wfp.find_orphan(File, "sub/dir/")
    assert is_orphan
    assert reqdir.get_state() == FileState.AWAITED


def test_define_step_reqdir_workdir(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    wfp.define_step(plan, "echo", workdir="sub/dir/")
    echo = wfp.find(Step, "echo  # wd=sub/dir/")
    action, workdir = echo.get_action_workdir()
    assert action == "echo"
    assert workdir == Path("sub/dir/")
    assert isinstance(workdir, Path)
    reqdir, is_orphan = wfp.find_orphan(File, "sub/dir/")
    assert is_orphan
    assert reqdir.get_state() == FileState.AWAITED


def test_amend_step_reqdir_out_path(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    wfp.define_step(plan, "echo")
    step = wfp.find(Step, "echo")
    wfp.amend_step(step, out_paths=["sub/dir/out"])
    reqdir, is_orphan = wfp.find_orphan(File, "sub/dir/")
    assert is_orphan
    assert reqdir.get_state() == FileState.AWAITED


def test_amend_step_reqdir_vol_path(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    wfp.define_step(plan, "echo")
    step = wfp.find(Step, "echo")
    wfp.amend_step(step, vol_paths=["sub/dir/vol"])
    reqdir, is_orphan = wfp.find_orphan(File, "sub/dir/")
    assert is_orphan
    assert reqdir.get_state() == FileState.AWAITED


def test_inp_paths(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    wfp.define_step(plan, "script", inp_paths=["foo"])
    step = wfp.find(Step, "script")
    assert set(step.inp_paths()) == {"./"}
    assert set(step.inp_paths(yield_orphan=True)) == {("./", False), ("foo", True)}
    assert list(step.inp_paths(yield_state=True)) == [("./", FileState.STATIC)]
    assert set(step.inp_paths(yield_state=True, yield_orphan=True)) == {
        ("./", FileState.STATIC, False),
        ("foo", FileState.AWAITED, True),
    }
    assert list(step.inp_paths(yield_hash=True)) == [("./", fake_hash("./"))]


def test_out_paths(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    wfp.define_step(plan, "script", out_paths=["foo", "bar"])
    step = wfp.find(Step, "script")
    wfp.update_file_hashes([("bar", fake_hash("bar"))], "succeeded")
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


def test_skip_amend_orphan_inputs(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    wfp.define_step(plan, "prog", out_paths=["bar"])
    step = wfp.find(Step, "prog")
    (foo1,) = declare_static(wfp, plan, ["foo"])

    # Simulate running the step, which amends a few things.
    assert wfp.job_queue.get_nowait().name == "EXECUTE: prog"
    wfp.amend_step(step, inp_paths=["foo"], env_vars=["AAA"], vol_paths=["bbb"])
    assert set(step.inp_paths(yield_orphan=True, yield_amended=True)) == {
        ("./", False, False),
        ("foo", False, True),
    }
    assert set(step.env_vars(yield_amended=True)) == {("AAA", True)}
    assert set(step.out_paths(yield_orphan=True, yield_amended=True)) == {
        ("bar", False, False),
    }
    assert set(step.vol_paths(yield_orphan=True, yield_amended=True)) == {
        ("bbb", False, True),
    }
    wfp.update_file_hashes([("bar", fake_hash("bar"))], "succeeded")
    step.completed(StepHash(b"step_ok", None, b"zzz", None))
    assert step.get_state() == StepState.SUCCEEDED
    assert step.get_hash() is not None

    # Make the static input orphan.
    foo1.orphan()
    assert foo1.is_orphan()
    # Amended info is not removed
    assert set(step.inp_paths(yield_orphan=True, yield_amended=True)) == {
        ("./", False, False),
        ("foo", True, True),
    }
    assert set(step.env_vars(yield_amended=True)) == {("AAA", True)}
    assert set(step.out_paths(yield_orphan=True, yield_amended=True)) == {
        ("bar", False, False),
    }
    assert set(step.vol_paths(yield_orphan=True, yield_amended=True)) == {
        ("bbb", False, True),
    }

    # Make step1 orphan
    step.orphan()
    assert step.is_alive()
    assert step.is_orphan()
    assert step.get_hash() is not None

    # Redefine the step in exactly the same way
    (foo2,) = declare_static(wfp, plan, ["foo"])
    assert foo1 == foo2
    wfp.define_step(plan, "prog", out_paths=["bar"])
    assert not step.is_orphan()
    assert set(step.inp_paths()) == {"./", "foo"}
    assert set(step.inp_paths(yield_orphan=True)) == {("./", False), ("foo", False)}
    assert set(step.out_paths()) == {"bar"}
    # Note that amended info is removed when inputs of a step are orphaned.
    assert set(step.vol_paths()) == {"bbb"}

    # Check that amended info is back and hash is still in place
    assert step.get_hash() is not None


def test_define_step_out_nested(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    wfp.define_step(plan, "script", out_paths=["sub/", "sub/foo/", "sub/foo/bar"])
    step = wfp.find(Step, "script")
    assert set(step.inp_paths()) == {"./"}
    assert set(step.out_paths()) == {"sub/", "sub/foo/", "sub/foo/bar"}


def test_define_step_vol_nested(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    wfp.define_step(plan, "script", out_paths=["sub/", "sub/foo/"], vol_paths=["sub/foo/bar"])
    step = wfp.find(Step, "script")
    assert set(step.inp_paths()) == {"./"}
    assert set(step.out_paths()) == {"sub/", "sub/foo/"}
    assert set(step.vol_paths()) == {"sub/foo/bar"}


def test_amend_step_out_nested(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    wfp.define_step(plan, "script", out_paths=["sub/"])
    step = wfp.find(Step, "script")
    wfp.amend_step(step, out_paths=["sub/foo/", "sub/foo/bar"])
    assert set(step.inp_paths()) == {"./"}
    assert set(step.out_paths()) == {"sub/", "sub/foo/", "sub/foo/bar"}


def test_amend_step_vol_nested(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    wfp.define_step(plan, "script", out_paths=["sub/"])
    step = wfp.find(Step, "script")
    wfp.amend_step(step, out_paths=["sub/foo/"], vol_paths=["sub/foo/bar"])
    assert set(step.inp_paths()) == {"./"}
    assert set(step.out_paths()) == {"sub/", "sub/foo/"}
    assert set(step.vol_paths()) == {"sub/foo/bar"}


def test_define_pool(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    wfp.define_pool(plan, "random", 2)
    assert list(plan.pool_definitions()) == [("random", 2)]


def test_step_deferred1(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    assert wfp.defer_glob(plan, "*.txt") == []
    inp_paths = ["test.png", "test.txt", "other.txt", "sub/boom.txt"]
    to_check = wfp.define_step(plan, "prog", inp_paths=inp_paths)
    assert to_check == [("other.txt", FileHash.unknown()), ("test.txt", FileHash.unknown())]

    # Check file nodes
    for path in "test.png", "sub/boom.txt":
        file, is_orphan = wfp.find_orphan(File, path)
        assert is_orphan
        assert file.get_state() == FileState.AWAITED
    for path in "test.txt", "other.txt":
        file, is_orphan = wfp.find_orphan(File, path)
        assert not is_orphan
        assert file.get_state() == FileState.MISSING


def test_step_deferred2(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    assert wfp.defer_glob(plan, "data/**") == []
    inp_paths = ["data/test.txt", "data.txt"]
    to_check = wfp.define_step(plan, "prog", inp_paths=inp_paths)
    assert to_check == [("data/", FileHash.unknown()), ("data/test.txt", FileHash.unknown())]

    # Check file nodes
    file, is_orphan = wfp.find_orphan(File, "data.txt")
    assert is_orphan
    assert file.get_state() == FileState.AWAITED
    for path in "data/", "data/test.txt":
        file, is_orphan = wfp.find_orphan(File, path)
        assert not is_orphan
        assert file.get_state() == FileState.MISSING


def test_step_deferred3(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    with wfp.con:
        assert wfp.defer_glob(plan, "data/**/foo.txt") == []
    with pytest.raises(GraphError), wfp.con:
        wfp.define_step(plan, "prog", inp_paths=["data/test/foo.txt"])
    declare_static(wfp, plan, ["data/", "data/other/"])
    to_check = wfp.define_step(plan, "prog", inp_paths=["data/other/foo.txt"])
    assert to_check == [("data/other/foo.txt", FileHash.unknown())]


def test_confirm_deferred(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    wfp.define_step(plan, "cat ${inp}", inp_paths=["test.txt"])
    step = wfp.find(Step, "cat ${inp}")
    to_check = wfp.declare_missing(plan, ["test.txt", "other.txt"])
    assert to_check == [("other.txt", FileHash.unknown()), ("test.txt", FileHash.unknown())]
    # static other.txt
    assert wfp.find(File, "other.txt").get_state() == FileState.MISSING
    wfp.update_file_hashes([("other.txt", fake_hash("other.txt"))], "confirmed")
    assert wfp.find(File, "other.txt").get_state() == FileState.STATIC
    assert step.get_state() == StepState.PENDING
    # static test.txt
    assert wfp.find(File, "test.txt").get_state() == FileState.MISSING
    wfp.update_file_hashes([("test.txt", fake_hash("test.txt"))], "confirmed")
    assert wfp.find(File, "test.txt").get_state() == FileState.STATIC
    assert step.get_state() == StepState.QUEUED


def test_step_try_clean(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")

    # Simulate execution of plan to get a hash
    step_hash = StepHash(b"p" * 64, None, b"p" * 64, None)
    plan.completed(step_hash)

    # Check presence of hash
    assert plan.get_hash() == step_hash

    # Run try_clean (via clean) and verify that hash has been removed.
    plan.orphan()
    wfp.clean()
    assert plan.get_hash() is None


def test_supply_parent(wfp: Workflow):
    declare_static(wfp, wfp.find(Step, "./plan.py"), ["../public/"])
    consumers = list(wfp.find(File, "./").consumers())
    parent = wfp.find(File, "../")
    assert parent in consumers


def test_step_lost_child(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    wfp.define_step(plan, "prog", out_paths=["data.txt"])
    step = wfp.find(Step, "prog")
    step.orphan()
    assert step.is_orphan()

    # Simulate creation of new data.txt
    to_check = wfp.declare_missing(wfp.root, ["data.txt"])
    assert to_check == [("data.txt", FileHash.unknown())]
    data = wfp.find(File, "data.txt")
    assert data.creator() == wfp.root

    # Check that step of prog is gone
    assert list(wfp.nodes(Step, include_orphans=True)) == [plan]


def test_deferred_glob_lost_child(wfp: Workflow):
    # Construct a workflow with a deferred glob
    plan = wfp.find(Step, "./plan.py")
    wfp.define_step(plan, "prog")
    prog = wfp.find(Step, "prog")
    wfp.defer_glob(prog, "*.txt")

    # Simulate the creation of a static data.txt through the deferred glob.
    to_check = wfp.define_step(prog, "work", inp_paths=["data.txt"])
    assert to_check == [("data.txt", FileHash.unknown())]
    wfp.update_file_hashes([("data.txt", fake_hash("data.txt"))], "confirmed")

    prog.orphan()
    assert prog.is_orphan()

    # Simulate creation of new data.txt
    to_check = wfp.declare_missing(wfp.root, ["data.txt"])
    assert to_check == [("data.txt", FileHash.unknown())]
    wfp.update_file_hashes([("data.txt", fake_hash("data.txt"))], "confirmed")
    data = wfp.find(File, "data.txt")
    assert data.creator() == wfp.root

    # Check that step of prog is gone
    assert list(wfp.nodes(DeferredGlob, include_orphans=True)) == []


def test_consistency_parent(wfp: Workflow):
    declare_static(wfp, wfp.find(Step, "./plan.py"), ["local.txt"])
    # Manually change local.txt to sub/local.txt
    wfp.con.execute("UPDATE node SET label = 'sub/local.txt' WHERE label = 'local.txt'")
    with pytest.raises(GraphError):
        wfp.check_consistency()
    # Manually set it back, because wfp will get checked by fixture...
    wfp.con.execute("UPDATE node SET label = 'local.txt' WHERE label = 'sub/local.txt'")


def test_consistency_succeeded_step(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    wfp.define_step(plan, "prog", out_paths=["out.txt"])
    step = wfp.find(Step, "prog")
    wfp.update_file_hashes([("out.txt", fake_hash("out.txt"))], "succeeded")
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


def test_sql_has_uncertain_creators(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    wfp.define_step(plan, "prog")
    prog = wfp.find(Step, "prog")
    assert wfp.con.execute(HAS_UNCERTAIN_CREATORS, (prog.i,)).fetchone()[0] == 0
    wfp.define_step(prog, "work")
    work = wfp.find(Step, "work")
    assert wfp.con.execute(HAS_UNCERTAIN_CREATORS, (work.i,)).fetchone()[0] == 1
    prog.set_state(StepState.RUNNING)
    assert wfp.con.execute(HAS_UNCERTAIN_CREATORS, (work.i,)).fetchone()[0] == 0


def test_sql_recurse_products_pending_simple(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    wfp.define_step(plan, "prog", inp_paths=["data.txt"])
    prog = wfp.find(Step, "prog")
    assert prog.get_state() == StepState.PENDING
    rows = wfp.con.execute(RECURSE_PRODUCTS_PENDING, (plan.i,)).fetchall()
    assert len(rows) == 1
    assert Step(wfp, *rows[0]) == prog
    rows = wfp.con.execute(RECURSE_PRODUCTS_PENDING, (prog.i,)).fetchall()
    assert len(rows) == 0


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
    step2.set_mandatory(Mandatory.NO)

    # Set the states so that there should be two pending steps that are potentially queuable.
    foo.set_state(StepState.RUNNING)
    bar.set_state(StepState.SUCCEEDED)
    spam.set_state(StepState.RUNNING)

    # Get the queuable pending steps.
    rows = wfp.con.execute(RECURSE_PRODUCTS_PENDING, (plan.i,)).fetchall()
    assert len(rows) == 2
    assert Step(wfp, *rows[0]) == egg
    assert Step(wfp, *rows[1]) == step1


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


def test_recurse_deferred_inputs1(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    wfp.define_step(plan, "prog", inp_paths=["data.txt"])
    prog = wfp.find(Step, "prog")
    wfp.defer_glob(plan, "*.txt")
    rows = wfp.con.execute(RECURSE_DEFERRED_INPUTS, (prog.i,)).fetchall()
    assert len(rows) == 1
    data = wfp.find(File, "data.txt")
    assert File(wfp, *rows[0]) == data


def test_recurse_deferred_inputs2(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    wfp.define_step(plan, "prog", inp_paths=["data/sub/deep.txt"])
    prog = wfp.find(Step, "prog")
    wfp.defer_glob(plan, "data/**")
    rows = wfp.con.execute(RECURSE_DEFERRED_INPUTS, (prog.i,)).fetchall()
    assert len(rows) == 3
    assert {row[1] for row in rows} == {"data/", "data/sub/", "data/sub/deep.txt"}


def test_recurse_deferred_inputs3(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    wfp.define_step(plan, "prog", inp_paths=["data/sub/other/deep.txt"])
    prog = wfp.find(Step, "prog")
    wfp.defer_glob(plan, "data/**/")
    wfp.declare_missing(plan, ["data/sub/other/deep.txt"])
    rows = wfp.con.execute(RECURSE_DEFERRED_INPUTS, (prog.i,)).fetchall()
    assert len(rows) == 3
    assert {row[1] for row in rows} == {"data/", "data/sub/", "data/sub/other/"}


def test_recreate_step_to_check(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    wfp.defer_glob(plan, "*.txt")
    to_check = wfp.define_step(plan, "prog", inp_paths=["data.txt"])
    assert to_check == [("data.txt", FileHash.unknown())]
    prog = wfp.find(Step, "prog")
    prog.orphan()
    to_check = wfp.define_step(plan, "prog", inp_paths=["data.txt"])
    assert to_check == [("data.txt", FileHash.unknown())]


def test_recreate_step_to_check_amend(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    wfp.defer_glob(plan, "d?/")
    to_check = wfp.define_step(
        plan,
        "prog",
        workdir="d0/",
        inp_paths=["d1/inp.txt"],
        out_paths=["d2/out.txt"],
        vol_paths=["d3/vol.txt"],
    )
    assert to_check == [
        ("d0/", FileHash.unknown()),
        ("d1/", FileHash.unknown()),
        ("d2/", FileHash.unknown()),
        ("d3/", FileHash.unknown()),
    ]
    prog = wfp.find(Step, "prog  # wd=d0/")
    prog.orphan()
    to_check = wfp.define_step(
        plan,
        "prog",
        workdir="d0/",
        inp_paths=["d1/inp.txt"],
        out_paths=["d2/out.txt"],
        vol_paths=["d3/vol.txt"],
    )
    assert to_check == [
        ("d0/", FileHash.unknown()),
        ("d1/", FileHash.unknown()),
        ("d2/", FileHash.unknown()),
        ("d3/", FileHash.unknown()),
    ]
    keep_going, to_check = wfp.amend_step(
        prog, inp_paths=["d4/inp.txt"], out_paths=["d5/out.txt"], vol_paths=["d6/vol.txt"]
    )
    assert not keep_going
    assert to_check == [
        ("d4/", FileHash.unknown()),
        ("d5/", FileHash.unknown()),
        ("d6/", FileHash.unknown()),
    ]


def test_get_file_hashes(wfp: Workflow):
    plan = wfp.find(Step, "./plan.py")
    paths = ["data.txt", "other.txt"]
    wfp.declare_missing(plan, paths)
    assert wfp.get_file_hashes(paths) == [
        ("data.txt", FileHash.unknown()),
        ("other.txt", FileHash.unknown()),
    ]
    wfp.update_file_hashes([("data.txt", fake_hash("data.txt"))], "confirmed")
    assert wfp.get_file_hashes(paths) == [
        ("data.txt", fake_hash("data.txt")),
        ("other.txt", FileHash.unknown()),
    ]
