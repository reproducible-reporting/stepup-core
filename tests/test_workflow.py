# StepUp Core provides the basic framework for the StepUp build tool.
# Copyright (C) 2024 Toon Verstraelen
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
"""Unit tests for stepup.core.workflow."""

from collections import Counter
from typing import cast

import pytest
from path import Path
from test_cascade import check_cascade_unstructure

from stepup.core.deferred_glob import DeferredGlob
from stepup.core.exceptions import GraphError
from stepup.core.file import File, FileState
from stepup.core.hash import FileHash, StepHash
from stepup.core.job import RunJob, TryReplayJob, ValidateAmendedJob
from stepup.core.nglob import NGlobMulti
from stepup.core.pytest import remove_hashes
from stepup.core.step import Mandatory, Step, StepRecording, StepState
from stepup.core.workflow import Workflow


def check_workflow_unstructure(workflow: Workflow) -> Workflow:
    workflow = cast(Workflow, check_cascade_unstructure(workflow))
    for step in workflow.get_steps():
        if step.get_state(workflow) == StepState.SUCCEEDED:
            assert step._hash is not None
            assert step._recording is not None
    return workflow


@pytest.fixture
def wfs() -> Workflow:
    """A workflow from scratch, no plan.py"""
    return Workflow.from_scratch()


@pytest.fixture
def wfp() -> Workflow:
    """A workflow with a boots step plan.py"""
    workflow = Workflow.from_scratch()
    workflow.declare_static("root:", ["plan.py", "./"])
    workflow.define_step("root:", "./plan.py", ["plan.py"])
    assert list(workflow.nodes) == ["root:", "vacuum:", "file:./", "file:plan.py", "step:./plan.py"]
    return workflow


def test_from_scratch(wfs: Workflow):
    assert wfs.node_classes["file"] == File
    assert wfs.node_classes["step"] == Step
    assert wfs.node_classes["dg"] == DeferredGlob
    assert wfs.unstructure() == {
        "nodes": [{"c": "root", "v": "v1"}, {"c": "vacuum"}],
        "products": [[0, 0], [0, 1]],
        "consumers": [],
    }
    assert wfs.kinds["root"] == {"root:"}
    assert wfs.kinds["vacuum"] == {"vacuum:"}
    check_workflow_unstructure(wfs)


def test_file(wfs: Workflow):
    wfs.declare_static("root:", ["./", "script.sh"])
    assert remove_hashes(wfs.unstructure()) == {
        "nodes": [
            {"c": "root", "v": "v1"},
            {"c": "vacuum"},
            {"c": "file", "p": 4, "s": FileState.STATIC.value},
            {"c": "file", "p": 5, "s": FileState.STATIC.value},
        ],
        "products": [[0, 0], [0, 1], [0, 2], [0, 3]],
        "consumers": [[2, 3]],
        "strings": ["./", "script.sh"],
    }
    check_workflow_unstructure(wfs)
    file2 = wfs.get_file("file:./")
    assert isinstance(file2, File)
    assert file2.path == "./"
    assert file2.key == "file:./"
    assert file2.get_state(wfs) == FileState.STATIC
    file3 = wfs.get_file("file:script.sh")
    assert isinstance(file3, File)
    assert file3.path == "script.sh"
    assert file3.key == "file:script.sh"
    assert file3.get_state(wfs) == FileState.STATIC
    assert wfs.get_files() == [file2, file3]
    with pytest.raises(TypeError):
        wfs.get_file("root:")

    # Verify things that should not be allowed
    with pytest.raises(GraphError):
        wfs.declare_static("root:", ["unknown/foo.txt"])


def test_step(wfs: Workflow):
    # Normal case
    step_key = wfs.define_step(
        "root:", "cp foo.txt sub/bar.txt", inp_paths=["foo.txt"], out_paths=["sub/bar.txt"]
    )
    assert step_key == "step:cp foo.txt sub/bar.txt"
    expected = {
        "nodes": [
            {"c": "root", "v": "v1"},
            {"c": "vacuum"},
            {
                "c": "step",
                "w": 7,
                "m": "cp foo.txt sub/bar.txt",
                "s": StepState.PENDING.value,
            },
            {"c": "file", "p": 7, "s": FileState.STATIC.value},
            {"c": "file", "p": 8, "s": FileState.PENDING.value},
            {"c": "file", "p": 9, "s": FileState.PENDING.value},
            {"c": "file", "p": 10, "s": FileState.PENDING.value},
        ],
        "products": [[0, 0], [0, 1], [0, 2], [0, 3], [1, 4], [1, 5], [2, 6]],
        "consumers": [[2, 6], [3, 2], [3, 4], [3, 5], [4, 2], [4, 6], [5, 2]],
        "strings": ["./", "sub/", "foo.txt", "sub/bar.txt"],
    }
    assert remove_hashes(wfs.unstructure()) == expected
    check_workflow_unstructure(wfs)
    assert wfs.get_step(step_key) == wfs.nodes[step_key]
    assert wfs.get_steps() == [wfs.nodes[step_key]]
    assert wfs.dir_queue.get_nowait() == (False, "./")
    with pytest.raises(TypeError):
        wfs.get_step("file:./")

    # Redefining the boot script is not allowed.
    with pytest.raises(GraphError):
        wfs.define_step(
            "root:", "cp foo.txt sub/bar.txt", inp_paths=["foo.txt"], out_paths=["sub/bar.txt"]
        )
    assert remove_hashes(wfs.unstructure()) == expected

    # Make the step RUNNING and test amending stuff.
    # (The extra inputs and outputs are not meant to be sensible for the copy command.)
    step = wfs.get_step(step_key)
    step.set_state(wfs, StepState.RUNNING)
    expected["nodes"][2]["s"] = StepState.RUNNING.value
    assert remove_hashes(wfs.unstructure()) == expected
    assert len(step.reschedule_due_to) == 0
    keep_going = wfs.amend_step(step_key, inp_paths=["spam.txt"], out_paths=["egg.csv"])
    assert not keep_going
    assert step.reschedule_due_to == {"spam.txt"}
    assert step.get_inp_paths(wfs) == ["./", "foo.txt", "spam.txt", "sub/"]
    assert step.get_out_paths(wfs) == ["egg.csv", "sub/bar.txt"]
    check_workflow_unstructure(wfs)
    state = wfs.unstructure()
    # Amend an input that was already known, which just gets ignored.
    wfs.amend_step(step_key, inp_paths=["foo.txt"])
    # Try a few things that should raise errors without changing the workflow.
    assert wfs.unstructure() == state
    with pytest.raises(GraphError):
        # Amend an output that was already known.
        wfs.amend_step(step_key, out_paths=["egg.csv"])
    assert wfs.unstructure() == state
    with pytest.raises(GraphError):
        # Amend a new input and an output that was already known.
        wfs.amend_step(step_key, inp_paths=["new.zip"], out_paths=["egg.csv"])
    assert wfs.unstructure() == state


def test_unstructure():
    state = {
        "nodes": [
            {"c": "root", "v": "v1"},
            {"c": "vacuum"},
            {
                "c": "step",
                "w": 8,
                "m": "touch sub/bar.txt",
                "s": StepState.PENDING.value,
            },
            {"c": "file", "p": 8, "s": FileState.STATIC.value, "h": ["d", 0, 0.0, 0, 0]},
            {"c": "file", "p": 9, "s": FileState.STATIC.value, "h": ["d", 0, 0.0, 0, 0]},
            {"c": "file", "p": 10, "s": FileState.STATIC.value, "h": ["d", 0, 0.0, 0, 0]},
            {
                "c": "file",
                "p": 11,
                "s": FileState.STATIC.value,
                "h": ["a", 0, 0.0, 0, 0],
            },
            {
                "c": "file",
                "p": 12,
                "s": FileState.PENDING.value,
                "h": ["b", 0, 0.0, 0, 0],
            },
        ],
        "products": [[0, 0], [0, 1], [0, 2], [0, 3], [0, 4], [0, 5], [1, 6], [2, 7]],
        "consumers": [[3, 2], [3, 4], [3, 5], [4, 6], [5, 7]],
        "strings": ["./", "blub/", "sub/", "blub/foo.txt", "sub/bar.txt"],
    }
    workflow = Workflow.structure(state)
    check_workflow_unstructure(workflow)


def test_simple_example(wfs: Workflow):
    # Create a runnable step and check the queue
    assert wfs.job_queue.qsize() == 0
    step_key = wfs.define_step(
        "root:", "cp foo.txt bar.txt", inp_paths=["foo.txt"], out_paths=["bar.txt"]
    )
    assert remove_hashes(wfs.unstructure()) == {
        "nodes": [
            {"c": "root", "v": "v1"},
            {"c": "vacuum"},
            {
                "c": "step",
                "w": 6,
                "m": "cp foo.txt bar.txt",
                "s": StepState.PENDING.value,
            },
            {"c": "file", "p": 6, "s": FileState.STATIC.value},
            {"c": "file", "p": 7, "s": FileState.PENDING.value},
            {"c": "file", "p": 8, "s": FileState.PENDING.value},
        ],
        "products": [[0, 0], [0, 1], [0, 2], [0, 3], [1, 4], [2, 5]],
        "consumers": [[2, 5], [3, 2], [3, 4], [3, 5], [4, 2]],
        "strings": ["./", "foo.txt", "bar.txt"],
    }
    wfs.declare_static("root:", ["foo.txt"])
    assert remove_hashes(wfs.unstructure()) == {
        "nodes": [
            {"c": "root", "v": "v1"},
            {"c": "vacuum"},
            {
                "c": "step",
                "w": 6,
                "m": "cp foo.txt bar.txt",
                "s": StepState.QUEUED.value,
            },
            {"c": "file", "p": 6, "s": FileState.STATIC.value},
            {"c": "file", "p": 7, "s": FileState.STATIC.value},
            {"c": "file", "p": 8, "s": FileState.PENDING.value},
        ],
        "products": [[0, 0], [0, 1], [0, 2], [0, 3], [0, 4], [2, 5]],
        "consumers": [[2, 5], [3, 2], [3, 4], [3, 5], [4, 2]],
        "strings": ["./", "foo.txt", "bar.txt"],
    }
    assert wfs.get_file_counters() == Counter({FileState.STATIC: 2, FileState.PENDING: 1})
    assert wfs.get_step_counters() == Counter({StepState.QUEUED: 1})

    # Verify things that should not be allowed
    with pytest.raises(GraphError):
        wfs.declare_static("root:", ["bar.txt"])

    # Mimic the runner, pretending to execute the step
    assert wfs.job_queue.get_nowait() == RunJob(step_key, None)
    wfs.get_step(step_key).completed(wfs, True, StepHash(b"mockhash", b"zzz"))
    assert remove_hashes(wfs.unstructure()) == {
        "nodes": [
            {"c": "root", "v": "v1"},
            {"c": "vacuum"},
            {
                "c": "step",
                "w": 6,
                "m": "cp foo.txt bar.txt",
                "s": StepState.SUCCEEDED.value,
            },
            {"c": "file", "p": 6, "s": FileState.STATIC.value},
            {"c": "file", "p": 7, "s": FileState.STATIC.value},
            {"c": "file", "p": 8, "s": FileState.BUILT.value},
        ],
        "products": [[0, 0], [0, 1], [0, 2], [0, 3], [0, 4], [2, 5]],
        "consumers": [[2, 5], [3, 2], [3, 4], [3, 5], [4, 2]],
        "strings": ["./", "foo.txt", "bar.txt"],
    }
    assert wfs.get_file_counters() == Counter({FileState.STATIC: 2, FileState.BUILT: 1})
    assert wfs.get_step_counters() == Counter({StepState.SUCCEEDED: 1})

    # Verify things that should not be allowed
    with pytest.raises(GraphError):
        wfs.declare_static("root:", ["foo.txt"])
    with pytest.raises(GraphError):
        wfs.declare_static("root:", ["bar.txt"])

    # orphan the static input and restore it
    wfs.orphan("file:foo.txt")
    wfs.orphan("file:./")
    assert remove_hashes(wfs.unstructure()) == {
        "nodes": [
            {"c": "root", "v": "v1"},
            {"c": "vacuum"},
            {
                "c": "step",
                "w": 6,
                "m": "cp foo.txt bar.txt",
                "s": StepState.PENDING.value,
            },
            {"c": "file", "p": 6, "s": FileState.STATIC.value},
            {"c": "file", "p": 7, "s": FileState.STATIC.value},
            {"c": "file", "p": 8, "s": FileState.PENDING.value},
        ],
        "products": [[0, 0], [0, 1], [0, 2], [1, 3], [1, 4], [2, 5]],
        "consumers": [[2, 5], [3, 2], [3, 4], [3, 5], [4, 2]],
        "strings": ["./", "foo.txt", "bar.txt"],
    }
    wfs.declare_static("root:", ["./"])
    assert remove_hashes(wfs.unstructure()) == {
        "nodes": [
            {"c": "root", "v": "v1"},
            {"c": "vacuum"},
            {
                "c": "step",
                "w": 6,
                "m": "cp foo.txt bar.txt",
                "s": StepState.PENDING.value,
            },
            {"c": "file", "p": 6, "s": FileState.STATIC.value},
            {"c": "file", "p": 7, "s": FileState.STATIC.value},
            {"c": "file", "p": 8, "s": FileState.PENDING.value},
        ],
        "products": [[0, 0], [0, 1], [0, 2], [0, 3], [1, 4], [2, 5]],
        "consumers": [[2, 5], [3, 2], [3, 4], [3, 5], [4, 2]],
        "strings": ["./", "foo.txt", "bar.txt"],
    }
    wfs.declare_static("root:", ["foo.txt"])
    assert remove_hashes(wfs.unstructure()) == {
        "nodes": [
            {"c": "root", "v": "v1"},
            {"c": "vacuum"},
            {
                "c": "step",
                "w": 6,
                "m": "cp foo.txt bar.txt",
                "s": StepState.QUEUED.value,
            },
            {"c": "file", "p": 6, "s": FileState.STATIC.value},
            {"c": "file", "p": 7, "s": FileState.STATIC.value},
            {"c": "file", "p": 8, "s": FileState.PENDING.value},
        ],
        "products": [[0, 0], [0, 1], [0, 2], [0, 3], [0, 4], [2, 5]],
        "consumers": [[2, 5], [3, 2], [3, 4], [3, 5], [4, 2]],
        "strings": ["./", "foo.txt", "bar.txt"],
    }


def test_define_boot_input_static(wfs: Workflow):
    echo_key = wfs.define_step("root:", "echo", ["foo.txt"])
    (foo_key,) = wfs.declare_static("root:", ["foo.txt"])
    assert not wfs.is_orphan(echo_key)
    assert wfs.get_consumers(foo_key) == [echo_key]
    assert wfs.get_suppliers(echo_key) == ["file:./", foo_key]


def test_define_boot_static_input(wfs: Workflow):
    (foo_key,) = wfs.declare_static("root:", ["foo.txt"])
    echo_key = wfs.define_step("root:", "echo", ["foo.txt"])
    assert not wfs.is_orphan(echo_key)
    assert wfs.get_consumers(foo_key) == [echo_key]
    assert wfs.get_suppliers(echo_key) == ["file:./", foo_key]


def test_redefine_boot(wfs: Workflow):
    step_key = wfs.define_step("root:", "echo 1")
    with pytest.raises(GraphError):
        wfs.define_step("root:", "echo 2")
    wfs.orphan(step_key)
    wfs.define_step("root:", "echo 3")


def test_define_boot_input_orphan(wfs: Workflow):
    wfs.define_step("root:", "echo", ["foo.txt"])
    assert wfs.is_orphan("file:foo.txt")


def test_redefine_step(wfp: Workflow):
    echo_key = wfp.define_step("step:./plan.py", "echo")
    assert wfp.get_step(echo_key).get_state(wfp) == StepState.QUEUED
    assert "step:echo" in list(wfp.nodes)
    with pytest.raises(GraphError):
        wfp.define_step("step:./plan.py", "echo")
    wfp.orphan("step:echo")
    wfp.define_step("step:./plan.py", "echo")
    assert wfp.get_step(echo_key).get_state(wfp) == StepState.QUEUED


def test_define_step_input_static(wfp: Workflow):
    cat_key = wfp.define_step("step:./plan.py", "cat given", ["given"])
    assert wfp.get_step(cat_key).get_state(wfp) == StepState.PENDING
    wfp.declare_static("step:./plan.py", ["given"])
    assert wfp.get_step(cat_key).get_state(wfp) == StepState.QUEUED


def test_define_step_static_input(wfp: Workflow):
    wfp.declare_static("step:./plan.py", ["given"])
    cat_key = wfp.define_step("step:./plan.py", "cat given", ["given"])
    assert wfp.get_step(cat_key).get_state(wfp) == StepState.QUEUED


def test_define_step_input_pending(wfp: Workflow):
    cat_key = wfp.define_step("step:./plan.py", "cat given", ["given"])
    cat_step = wfp.get_step(cat_key)
    assert cat_step.get_state(wfp) == StepState.PENDING
    assert wfp.is_orphan("file:given")
    file = wfp.get_file("file:given")
    assert file.get_state(wfp) == FileState.PENDING
    touch_key = wfp.define_step("step:./plan.py", "touch given", out_paths=["given"])
    touch_step = wfp.get_step(touch_key)
    assert touch_step.get_state(wfp) == StepState.QUEUED
    assert file.get_state(wfp) == FileState.PENDING
    touch_step.completed(wfp, True, StepHash(b"mock", b"zzz"))
    assert touch_step.get_state(wfp) == StepState.SUCCEEDED
    assert file.get_state(wfp) == FileState.BUILT
    assert cat_step.get_state(wfp) == StepState.QUEUED


def test_define_step_pending_input(wfp: Workflow):
    touch_key = wfp.define_step("step:./plan.py", "touch given", out_paths=["given"])
    touch_step = wfp.get_step(touch_key)
    assert touch_step.get_state(wfp) == StepState.QUEUED
    assert not wfp.is_orphan("file:given")
    file = wfp.get_file("file:given")
    assert file.get_state(wfp) == FileState.PENDING
    cat_key = wfp.define_step("step:./plan.py", "cat given", ["given"])
    cat_step = wfp.get_step(cat_key)
    assert cat_step.get_state(wfp) == StepState.PENDING
    assert file.get_state(wfp) == FileState.PENDING
    assert touch_step.get_state(wfp) == StepState.QUEUED
    touch_step.completed(wfp, True, StepHash(b"mock", b"zzz"))
    assert touch_step.get_state(wfp) == StepState.SUCCEEDED
    assert file.get_state(wfp) == FileState.BUILT
    assert cat_step.get_state(wfp) == StepState.QUEUED


def test_define_step_built_input(wfp: Workflow):
    touch_key = wfp.define_step("step:./plan.py", "touch given", out_paths=["given"])
    touch_step = wfp.get_step(touch_key)
    assert touch_step.get_state(wfp) == StepState.QUEUED
    assert not wfp.is_orphan("file:given")
    file = wfp.get_file("file:given")
    assert file.get_state(wfp) == FileState.PENDING
    touch_step.completed(wfp, True, StepHash(b"mock", b"zzz"))
    assert touch_step.get_state(wfp) == StepState.SUCCEEDED
    assert file.get_state(wfp) == FileState.BUILT
    cat_key = wfp.define_step("step:./plan.py", "cat given", ["given"])
    cat_step = wfp.get_step(cat_key)
    assert cat_step.get_state(wfp) == StepState.QUEUED


def test_define_step_volatile_input(wfp: Workflow):
    touch_key = wfp.define_step("step:./plan.py", "touch given", vol_paths=["given"])
    touch_step = wfp.get_step(touch_key)
    assert touch_step.get_state(wfp) == StepState.QUEUED
    assert not wfp.is_orphan("file:given")
    file = wfp.get_file("file:given")
    assert file.get_state(wfp) == FileState.VOLATILE
    with pytest.raises(GraphError):
        # Volatile files are not allowed as inputs
        wfp.define_step("step:./plan.py", "cat given", ["given"])
    touch_step.completed(wfp, True, StepHash(b"mock", b"zzz"))
    assert touch_step.get_state(wfp) == StepState.SUCCEEDED
    assert file.get_state(wfp) == FileState.VOLATILE
    with pytest.raises(GraphError):
        # Volatile files are not allowed as inputs
        wfp.define_step("step:./plan.py", "cat given", ["given"])


def test_define_step_input_volatile(wfp: Workflow):
    cat_key = wfp.define_step("step:./plan.py", "cat given", ["given"])
    cat_step = wfp.get_step(cat_key)
    assert cat_step.get_state(wfp) == StepState.PENDING
    assert wfp.is_orphan("file:given")
    file = wfp.get_file("file:given")
    assert file.get_state(wfp) == FileState.PENDING
    with pytest.raises(GraphError):
        wfp.define_step("step:./plan.py", "touch given", vol_paths=["given"])


def test_file_state_static_overlap(wfp: Workflow):
    wfp.declare_static("step:./plan.py", ["given"])
    with pytest.raises(GraphError):
        wfp.define_step("step:./plan.py", "touch given", out_paths=["given"])
    with pytest.raises(GraphError):
        wfp.define_step("step:./plan.py", "touch given", vol_paths=["given"])
    step_key = wfp.define_step("step:./plan.py", "echo", inp_paths=["some"], out_paths=["other"])
    wfp.get_step(step_key).set_state(wfp, StepState.RUNNING)
    assert not wfp.amend_step(step_key, inp_paths=["inp"], out_paths=["out"], vol_paths=["vol"])
    # Amending an existing input is tolerated.
    wfp.amend_step(step_key, inp_paths=["some"])
    with pytest.raises(GraphError):
        wfp.amend_step(step_key, out_paths=["given"])
    with pytest.raises(GraphError):
        wfp.amend_step(step_key, vol_paths=["given"])


def test_file_state_output_overlap(wfp: Workflow):
    wfp.define_step("step:./plan.py", "touch given", out_paths=["given"])
    with pytest.raises(GraphError):
        wfp.declare_static("step:./plan.py", ["given"])
    with pytest.raises(GraphError):
        wfp.define_step("step:./plan.py", "touch given", vol_paths=["given"])
    step_key = wfp.define_step("step:./plan.py", "echo", inp_paths=["some"], out_paths=["other"])
    wfp.get_step(step_key).set_state(wfp, StepState.RUNNING)
    assert not wfp.amend_step(
        step_key, inp_paths=["inp", "given"], out_paths=["out"], vol_paths=["vol"]
    )
    with pytest.raises(GraphError):
        wfp.amend_step(step_key, out_paths=["given"])
    with pytest.raises(GraphError):
        wfp.amend_step(step_key, vol_paths=["given"])


def test_file_state_volatile_overlap(wfp: Workflow):
    wfp.define_step("step:./plan.py", "touch given", vol_paths=["given"])
    with pytest.raises(GraphError):
        wfp.declare_static("step:./plan.py", ["given"])
    with pytest.raises(GraphError):
        wfp.define_step("step:./plan.py", "touch given", out_paths=["given"])
    step_key = wfp.define_step("step:./plan.py", "echo", inp_paths=["some"], out_paths=["other"])
    wfp.get_step(step_key).set_state(wfp, StepState.RUNNING)
    assert not wfp.amend_step(step_key, inp_paths=["inp"], out_paths=["out"], vol_paths=["vol"])
    with pytest.raises(GraphError):
        wfp.amend_step(step_key, inp_paths=["given"])
    with pytest.raises(GraphError):
        wfp.amend_step(step_key, out_paths=["given"])
    with pytest.raises(GraphError):
        wfp.amend_step(step_key, vol_paths=["given"])


def test_volatile_directory(wfp: Workflow):
    with pytest.raises(GraphError):
        wfp.define_step("step:./plan.py", "touch given", vol_paths=["given/"])


def test_define_queued_step_no_pool(wfp: Workflow):
    plan_key = "step:./plan.py"
    step_key = wfp.define_step(plan_key, "touch given", vol_paths=["given"])
    step = wfp.get_step(step_key)
    assert step.get_state(wfp) == StepState.QUEUED
    assert wfp.job_queue.get_nowait() == RunJob(plan_key, None)
    assert wfp.job_queue.get_nowait() == RunJob(step_key, None)


def test_define_queued_step_pool(wfp: Workflow):
    plan_key = "step:./plan.py"
    step_key = wfp.define_step(plan_key, "touch given", out_paths=["given"], pool="aa")
    step = wfp.get_step(step_key)
    assert step.get_state(wfp) == StepState.QUEUED
    assert wfp.job_queue.get_nowait() == RunJob(plan_key, None)
    assert wfp.job_queue.get_nowait() == RunJob(step_key, "aa")


def test_define_queued_step_replay():
    state = {
        "nodes": [
            {"c": "root", "v": "v1"},
            {"c": "vacuum"},
            {"c": "file", "p": 6, "s": FileState.STATIC.value, "h": [b"d", 0, 0.0, 0, 0]},
            {
                "c": "step",
                "w": 6,
                "m": "cat < inp > out",
                "s": StepState.SUCCEEDED.value,
                "p": "bb",
                "h": [b"mock", b"aaa"],
            },
            {"c": "file", "p": 7, "s": FileState.STATIC.value, "h": [b"foo", 0, 0.0, 0, 0]},
            {"c": "file", "p": 8, "s": FileState.BUILT.value, "h": [b"bar", 0, 0.0, 0, 0]},
        ],
        "products": [[0, 0], [0, 1], [0, 2], [0, 3], [0, 4], [3, 5]],
        "consumers": [[2, 3], [2, 4], [2, 5], [3, 5], [4, 3]],
        "strings": ["./", "inp", "out"],
    }
    workflow = Workflow.structure(state)
    step_key = "step:cat < inp > out"
    step = workflow.get_step(step_key)
    assert step.get_state(workflow) == StepState.SUCCEEDED
    assert step.hash.digest == b"mock"
    assert step.hash.inp_digest == b"aaa"
    assert isinstance(step.recording, StepRecording)
    workflow.process_watcher_changes(set(), {Path("inp")})
    assert step.get_state(workflow) == StepState.QUEUED
    out = workflow.get_file("file:out")
    assert out.get_state(workflow) == FileState.PENDING
    assert workflow.job_queue.get_nowait() == TryReplayJob(step_key, "bb")
    step.replay_rest(workflow)
    assert workflow.unstructure() == state
    workflow.discard_recordings()
    assert step.hash is None
    assert step.recording is None


def test_define_queued_step_replay_extra(wfp):
    # Prepare jobs for normal run
    plan_key = "step:./plan.py"
    plan = wfp.get_step(plan_key)
    assert wfp.job_queue.get_nowait() == RunJob(plan_key, None)
    wfp.declare_static(plan_key, ["ainp", "ainp2"])
    wfp.job_queue_changed.clear()
    foo_key = wfp.define_step(plan_key, "foo > log", env_vars={"VAR": "VALUE"}, out_paths=["log"])
    foo = wfp.get_step(foo_key)
    assert foo.get_state(wfp) == StepState.QUEUED
    assert wfp.job_queue_changed.is_set()
    bar_key = wfp.define_step(
        foo_key, "bar > spam", inp_paths=["log"], env_vars={"X": "Y"}, vol_paths=["spam"]
    )
    bar = wfp.get_step(bar_key)
    assert bar.get_state(wfp) == StepState.PENDING
    plan.completed(wfp, True, StepHash(b"plan_ok", b"zzz"))

    # Simulate run
    # foo
    assert wfp.job_queue.get_nowait() == RunJob(foo_key, None)
    wfp.amend_step(foo_key, inp_paths=["ainp"], out_paths=["aout"], vol_paths=["avol"])
    foo.completed(wfp, True, StepHash(b"foo_ok", b"zzz"))
    assert foo.get_state(wfp) == StepState.SUCCEEDED
    assert bar.get_state(wfp) == StepState.QUEUED
    # bar
    assert wfp.job_queue.get_nowait() == RunJob(bar_key, None)
    wfp.amend_step(bar_key, inp_paths=["ainp2"], out_paths=["aout2"], vol_paths=["avol2"])
    assert "file:ainp2" in wfp.suppliers[bar_key]
    bar.completed(wfp, True, StepHash(b"bar_ok", b"zzz"))
    assert bar.get_state(wfp) == StepState.SUCCEEDED
    check_workflow_unstructure(wfp)
    state1 = wfp.unstructure()

    # Make foo pending and check state
    wfp.job_queue_changed.clear()
    foo.make_pending(wfp)
    assert not wfp.job_queue_changed.is_set()
    assert foo.hash is not None
    assert isinstance(foo.recording, StepRecording)
    assert foo.get_state(wfp) == StepState.PENDING
    assert not foo.validate_amended
    assert wfp.get_file("file:log").get_state(wfp) == FileState.PENDING
    assert wfp.is_orphan(bar_key)
    assert bar.hash is not None
    assert isinstance(bar.recording, StepRecording)
    assert bar.get_state(wfp) == StepState.PENDING
    assert bar.validate_amended
    check_workflow_unstructure(wfp)

    # Simulate rerun
    foo.queue_if_appropriate(wfp)
    assert wfp.job_queue_changed.is_set()
    assert foo.get_state(wfp) == StepState.QUEUED
    assert bar.get_state(wfp) == StepState.PENDING
    assert wfp.job_queue.get_nowait() == TryReplayJob(foo_key, None)
    foo.clean_before_run(wfp)
    foo.replay_amend(wfp)
    foo.replay_rest(wfp)
    assert foo.get_state(wfp) == StepState.SUCCEEDED
    assert bar.get_state(wfp) == StepState.QUEUED
    assert wfp.job_queue.get_nowait() == TryReplayJob(bar_key, None)
    bar.clean_before_run(wfp)
    bar.replay_amend(wfp)
    bar.replay_rest(wfp)
    assert bar.get_state(wfp) == StepState.SUCCEEDED
    check_workflow_unstructure(wfp)
    assert wfp.unstructure() == state1


def test_replay_step_amended_orphaned_input(wfp):
    # Prepare jobs for normal run
    plan_key = "step:./plan.py"
    assert wfp.job_queue.get_nowait() == RunJob(plan_key, None)
    (ainp_key,) = wfp.declare_static(plan_key, ["ainp"])
    wfp.job_queue_changed.clear()
    foo_key = wfp.define_step(plan_key, "foo > log", out_paths=["log"])
    foo = wfp.get_step(foo_key)
    assert foo.get_state(wfp) == StepState.QUEUED
    assert foo.get_out_paths(wfp) == ["log"]
    assert wfp.job_queue_changed.is_set()

    # Simulate run
    assert wfp.job_queue.get_nowait() == RunJob(foo_key, None)
    wfp.amend_step(foo_key, inp_paths=["ainp"], out_paths=["aout"], vol_paths=["avol"])
    foo.completed(wfp, True, StepHash(b"foo_ok", b"zzz"))
    assert foo.get_state(wfp) == StepState.SUCCEEDED
    check_workflow_unstructure(wfp)
    state1 = wfp.unstructure()

    # Make ainp orphan and check state
    wfp.job_queue_changed.clear()
    assert foo.get_out_paths(wfp) == ["aout", "log"]
    wfp.orphan(ainp_key)
    assert foo.get_out_paths(wfp) == ["log"]
    assert not wfp.job_queue_changed.is_set()
    assert foo.hash is not None
    assert isinstance(foo.recording, StepRecording)
    assert foo.get_state(wfp) == StepState.PENDING
    assert wfp.get_file("file:log").get_state(wfp) == FileState.PENDING
    check_workflow_unstructure(wfp)

    # Replay
    wfp.declare_static(plan_key, ["ainp"])
    foo.queue_if_appropriate(wfp)
    assert wfp.job_queue_changed.is_set()
    assert foo.get_state(wfp) == StepState.QUEUED
    assert wfp.job_queue.get_nowait() == TryReplayJob(foo_key, None)
    foo.clean_before_run(wfp)
    foo.replay_amend(wfp)
    assert isinstance(foo.recording, StepRecording)
    foo.replay_rest(wfp)
    assert foo.get_state(wfp) == StepState.SUCCEEDED
    assert not wfp.is_orphan("file:log")
    assert wfp.get_file("file:log").get_state(wfp) == FileState.BUILT
    check_workflow_unstructure(wfp)
    assert wfp.unstructure() == state1


def test_replay_ngm(wfp: Workflow):
    # Prepare jobs for normal run
    plan_key = "step:./plan.py"
    plan = wfp.get_step(plan_key)
    assert wfp.job_queue.get_nowait() == RunJob(plan_key, None)
    wfp.job_queue_changed.clear()
    foo_key = wfp.define_step(plan_key, "foo")
    foo = wfp.get_step(foo_key)
    assert foo.get_state(wfp) == StepState.QUEUED
    assert wfp.job_queue_changed.is_set()
    plan.completed(wfp, True, StepHash(b"plan_ok", b"ee"))
    assert plan.get_state(wfp) == StepState.SUCCEEDED

    # Simulate run
    assert wfp.job_queue.get_nowait() == RunJob(foo_key, None)
    ngm = NGlobMulti.from_patterns(["${*prefix}_data.txt"], subs={"prefix": "n???"})
    wfp.register_nglob(foo_key, ngm)
    foo.completed(wfp, True, StepHash(b"foo_ok", b"zzz"))
    assert foo.get_state(wfp) == StepState.SUCCEEDED
    check_workflow_unstructure(wfp)

    # Make foo pending and check state
    wfp.job_queue_changed.clear()
    foo.make_pending(wfp)
    assert not wfp.job_queue_changed.is_set()
    assert foo.hash is not None
    assert isinstance(foo.recording, StepRecording)
    assert foo.get_state(wfp) == StepState.PENDING

    # Replay
    foo.queue_if_appropriate(wfp)
    assert wfp.job_queue_changed.is_set()
    assert foo.get_state(wfp) == StepState.QUEUED
    assert wfp.job_queue.get_nowait() == TryReplayJob(foo_key, None)
    foo.clean_before_run(wfp)
    foo.replay_amend(wfp)
    foo.replay_rest(wfp)
    assert foo.get_state(wfp) == StepState.SUCCEEDED
    check_workflow_unstructure(wfp)
    assert len(foo.nglob_multis) == 1
    assert len(foo.nglob_multis[0].nglob_singles) == 1
    assert foo.nglob_multis[0].nglob_singles[0].pattern == "${*prefix}_data.txt"
    assert foo.nglob_multis[0].nglob_singles[0].subs == {"prefix": "n???"}
    assert foo.nglob_multis[0].used_names == ("prefix",)
    assert foo.nglob_multis[0].subs == {"prefix": "n???"}


def test_amend_step(wfp: Workflow):
    plan_key = "step:./plan.py"
    step_key = wfp.define_step(plan_key, "blub > log", vol_paths=["log"])
    step = wfp.get_step(step_key)
    assert step.get_state(wfp) == StepState.QUEUED
    assert wfp.job_queue.get_nowait() == RunJob(plan_key, None)
    assert wfp.job_queue.get_nowait() == RunJob(step_key, None)
    assert wfp.amend_step(step_key)
    assert not wfp.amend_step(
        step_key, inp_paths=["inp1", "inp2"], out_paths=["out3"], vol_paths=["vol4"]
    )
    assert step.amended_suppliers == {"file:inp1", "file:inp2"}
    assert step.amended_consumers == {"file:out3", "file:vol4"}
    step.completed(wfp, False, StepHash(b"fail", b"inp_fail"))
    step.set_state(wfp, StepState.PENDING)
    wfp.declare_static("step:./plan.py", ["inp1"])
    step.set_state(wfp, StepState.PENDING)
    wfp.declare_static("step:./plan.py", ["inp2"])
    step.set_state(wfp, StepState.QUEUED)
    assert wfp.get_products(step_key) == ["file:log", "file:out3", "file:vol4"]
    assert wfp.job_queue.get_nowait() == ValidateAmendedJob(step_key, None)


def test_define_queued_step_replay_amended():
    state = {
        "nodes": [
            {"c": "root", "v": "v1"},
            {"c": "vacuum"},
            {"c": "file", "p": 10, "s": FileState.STATIC.value, "h": [b"d", 0, 0.0, 0, 0]},
            {
                "c": "step",
                "w": 10,
                "m": "foo < inp > out 2> vol",
                "s": StepState.SUCCEEDED.value,
                "p": "bb",
                "h": [b"boo", b"www"],
                "as": [7],
                "ac": [8, 9],
            },
            {"c": "file", "p": 11, "s": FileState.STATIC.value, "h": [b"foo", 0, 0.0, 0, 0]},
            {"c": "file", "p": 12, "s": FileState.BUILT.value, "h": [b"bar", 0, 0.0, 0, 0]},
            {"c": "file", "p": 13, "s": FileState.BUILT.value, "h": [b"bar", 0, 0.0, 0, 0]},
            {"c": "file", "p": 14, "s": FileState.STATIC.value, "h": [b"foo", 0, 0.0, 0, 0]},
            {"c": "file", "p": 15, "s": FileState.BUILT.value, "h": [b"bar", 0, 0.0, 0, 0]},
            {"c": "file", "p": 16, "s": FileState.BUILT.value, "h": [b"bar", 0, 0.0, 0, 0]},
        ],
        "products": [
            [0, 0],
            [0, 1],
            [0, 2],
            [0, 3],
            [0, 4],
            [0, 7],
            [3, 5],
            [3, 6],
            [3, 8],
            [3, 9],
        ],
        "consumers": [
            [2, 3],
            [2, 4],
            [2, 5],
            [2, 6],
            [2, 7],
            [2, 8],
            [2, 9],
            [3, 5],
            [3, 6],
            [3, 8],
            [3, 9],
            [4, 3],
            [7, 3],
        ],
        "strings": ["./", "inp", "out", "vol", "ainp", "aout", "avol"],
    }
    workflow = Workflow.structure(state)
    step_key = "step:foo < inp > out 2> vol"
    step = workflow.get_step(step_key)
    assert step.get_state(workflow) == StepState.SUCCEEDED
    assert step.hash.digest == b"boo"
    assert step.hash.inp_digest == b"www"
    assert isinstance(step.recording, StepRecording)
    workflow.process_watcher_changes(set(), {Path("ainp")})
    assert step.get_state(workflow) == StepState.QUEUED
    out = workflow.get_file("file:out")
    assert out.get_state(workflow) == FileState.PENDING
    assert workflow.job_queue.get_nowait() == TryReplayJob(step_key, "bb")
    step.clean_before_run(workflow)
    assert "file:ainp" not in workflow.get_suppliers(step_key, include_orphans=True)
    assert workflow.is_orphan("file:aout")
    assert workflow.is_orphan("file:avol")
    step.replay_amend(workflow)
    assert "file:ainp" in workflow.get_suppliers(step_key, include_orphans=True)
    assert not workflow.is_orphan("file:aout")
    assert not workflow.is_orphan("file:avol")
    assert workflow.get_creator("file:aout") == step_key
    assert workflow.get_creator("file:avol") == step_key
    step.replay_rest(workflow)
    assert workflow.unstructure() == state
    workflow.discard_recordings()
    assert step.hash is None
    assert step.recording is None


def test_register_nglob(wfp: Workflow):
    plan_key = "step:./plan.py"
    step_key = wfp.define_step(plan_key, "touch log", vol_paths=["log"])
    step = wfp.get_step(step_key)
    ngm = NGlobMulti.from_patterns(["*.txt"])
    wfp.register_nglob(step_key, ngm)
    assert ngm in step.nglob_multis
    assert step_key in wfp.step_keys_with_nglob
    wfp.orphan(step_key)
    assert ngm in step.nglob_multis
    assert step_key in wfp.step_keys_with_nglob
    wfp.clean()
    assert ngm not in step.nglob_multis
    assert step_key not in wfp.step_keys_with_nglob


def test_is_relevant(wfp: Workflow):
    assert wfp.is_relevant("plan.py")
    assert wfp.is_relevant("./")
    assert not wfp.is_relevant("unknown.txt")
    wfp.register_nglob("step:./plan.py", NGlobMulti.from_patterns(["*.txt"]))
    assert wfp.is_relevant("unknown.txt")


def test_watcher_update(wfp: Workflow):
    plan_key = "step:./plan.py"
    wfp.declare_static(plan_key, ["aa1_foo.txt", "bb7_foo.txt", "cc5_foo.txt"])
    ngm = NGlobMulti.from_patterns(
        ["${*prefix}_foo.txt", "${*prefix}_bar.txt"],
        {"prefix": "??[0-9]", "unused": "aa??"},
    )
    wfp.register_nglob(plan_key, ngm)
    ngm.extend(["aa1_foo.txt", "aa1_bar.txt", "bb7_foo.txt", "cc5_foo.txt"])
    step_key = wfp.define_step(
        plan_key, "test", inp_paths=["aa1_foo.txt"], out_paths=["aa1_bar.txt"], vol_paths=["log"]
    )
    step = wfp.get_step(step_key)
    assert wfp.get_creator("file:aa1_bar.txt") == step_key
    assert step.get_state(wfp) == StepState.QUEUED
    step.completed(wfp, False, StepHash(b"fail", b"inp_fail"))
    assert step.get_state(wfp) == StepState.FAILED

    with pytest.raises(ValueError):
        wfp.process_watcher_changes(["cc5_foo.txt"], ["cc5_foo.txt"])
    with pytest.raises(ValueError):
        wfp.process_watcher_changes(["zz0_bar.txt"], [])
    wfp.process_watcher_changes(["cc5_foo.txt"], ["aa1_bar.txt", "bb7_bar.txt"])

    # The top-level plan became pending, so the step becomes orphan.
    assert wfp.is_orphan(step_key)
    assert wfp.get_file("file:aa1_bar.txt").get_state(wfp) == FileState.PENDING
    assert step.get_state(wfp) == StepState.PENDING
    assert wfp.is_orphan("file:cc5_foo.txt")
    assert wfp.get_file("file:cc5_foo.txt").get_state(wfp) == FileState.MISSING
    assert "file:bb7_bar.txt" not in wfp.nodes
    assert ngm.files() == ("aa1_bar.txt", "aa1_foo.txt", "bb7_bar.txt", "bb7_foo.txt")
    assert ngm.nglob_singles[0].results == {("aa1",): {"aa1_foo.txt"}, ("bb7",): {"bb7_foo.txt"}}
    assert ngm.nglob_singles[1].results == {("aa1",): {"aa1_bar.txt"}, ("bb7",): {"bb7_bar.txt"}}


def test_watcher_updated_static_orphan(wfp):
    plan_key = "step:./plan.py"
    wfp.declare_static(plan_key, ["foo.txt"])
    wfp.orphan("file:foo.txt")
    wfp.process_watcher_changes({}, {"foo.txt"})


def test_watcher_deleted_static_orphan(wfp):
    plan_key = "step:./plan.py"
    wfp.declare_static(plan_key, ["foo.txt"])
    wfp.orphan("file:foo.txt")
    wfp.process_watcher_changes({"foo.txt"}, {})


def test_watcher_updated_built_orphan(wfp):
    plan_key = "step:./plan.py"
    step_key = wfp.define_step(plan_key, "touch foo.txt", out_paths=["foo.txt"])
    wfp.orphan(step_key)
    wfp.process_watcher_changes({}, {"foo.txt"})


def test_watcher_deleted_built_orphan(wfp):
    plan_key = "step:./plan.py"
    step_key = wfp.define_step(plan_key, "touch foo.txt", out_paths=["foo.txt"])
    wfp.orphan(step_key)
    wfp.process_watcher_changes({"foo.txt"}, {})


def test_directory_usage(wfs: Workflow):
    assert wfs.dir_queue.empty()
    wfs.declare_static("root:", ["./"])
    assert wfs.dir_queue.get_nowait() == (False, "./")
    wfs.declare_static("root:", ["foo.txt"])
    assert wfs.dir_queue.empty()
    wfs.orphan("file:foo.txt")
    assert wfs.dir_queue.empty()
    wfs.orphan("file:./")
    assert wfs.dir_queue.empty()
    wfs.clean()
    assert wfs.dir_queue.get_nowait() == (True, "./")


def test_parent_must_exist():
    # Load workflow from graph with files lacking parents. This should raise an error
    state = {
        "nodes": [
            {"c": "root", "v": "v1"},
            {"c": "vacuum"},
            {"c": "file", "p": 3, "s": FileState.STATIC.value, "h": [b"foo", 0, 0.0, 0, 0]},
        ],
        "products": [[0, 0], [0, 1], [0, 2]],
        "consumers": [],
        "strings": ["sub/inp"],
    }
    wf = Workflow.structure(state)
    with pytest.raises(ValueError):
        wf.check_consistency()


def test_parent_stays_alive(wfp: Workflow):
    # When a parent directory is orphaned,
    # it cannot be cleaned until all files or subdirectories are orphaned.
    plan_key = "step:./plan.py"
    wfp.declare_static(plan_key, ["sub/", "sub/foo"])
    wfp.orphan("file:sub/")
    assert wfp.is_orphan("file:sub/")
    wfp.clean()
    assert "file:sub/" in wfp.nodes
    assert wfp.is_orphan("file:sub/")
    wfp.orphan("file:sub/foo")
    assert wfp.is_orphan("file:sub/foo")
    assert "file:sub/" in wfp.file_states
    assert "file:sub/foo" in wfp.file_states
    wfp.clean()
    assert "file:sub/" not in wfp.nodes
    assert "file:sub/foo" not in wfp.nodes
    assert "file:sub/" not in wfp.file_states
    assert "file:sub/foo" not in wfp.file_states


def test_to_be_deleted(wfp: Workflow):
    plan_key = "step:./plan.py"
    wfp.declare_static(plan_key, ["static"])
    wfp.define_step(plan_key, "blub1", out_paths=["built", "gone"])
    wfp.define_step(plan_key, "blub2", vol_paths=["volatile"])
    wfp.define_step(plan_key, "blub3", out_paths=["pending"])
    wfp.define_step(plan_key, "mkdir sub", out_paths=["sub/"])
    gone_file_hash = FileHash(b"mockg", 0, 0.0, 0, 0)
    wfp.set_file_hash("gone", gone_file_hash)
    built_file_hash = FileHash(b"mockb", 0, 0.0, 0, 0)
    wfp.set_file_hash("built", built_file_hash)
    sub_file_hash = FileHash(b"d", 0, 0.0, 0, 0)
    wfp.set_file_hash("sub/", sub_file_hash)
    wfp.get_step("step:blub1").completed(wfp, True, StepHash(b"aaa", b"zzz"))
    wfp.orphan(plan_key)
    assert wfp.to_be_deleted == []
    assert "step:./plan.py" in wfp.step_states
    wfp.clean()
    assert wfp.to_be_deleted == [
        ("built", built_file_hash),
        ("gone", gone_file_hash),
        ("sub/", sub_file_hash),
        ("volatile", None),
    ]
    assert "step:./plan.py" not in wfp.step_states


def test_watcher_deleted(wfp):
    plan_key = "step:./plan.py"
    (tst_key,) = wfp.declare_static("root:", ["tst"])
    step1_key = wfp.define_step(plan_key, "bla1", out_paths=["prr"])
    step2_key = wfp.define_step(plan_key, "bla2", ["prr"])

    # Static
    tst_file = wfp.get_file(tst_key)
    tst_file.watcher_deleted(wfp)
    assert tst_file.get_state(wfp) == FileState.MISSING
    with pytest.raises(ValueError):
        tst_file.watcher_deleted(wfp)

    # Built
    prr_key = "file:prr"
    prr_file = wfp.get_file(prr_key)
    assert prr_file.get_state(wfp) == FileState.PENDING
    prr_file.watcher_deleted(wfp)
    assert prr_file.get_state(wfp) == FileState.PENDING
    step1 = wfp.get_step(step1_key)
    step1.completed(wfp, True, StepHash(b"11", b"zzz"))
    step2 = wfp.get_step(step2_key)
    step2.completed(wfp, False, StepHash(b"fail", b"inp_fail"))
    assert prr_file.get_state(wfp) == FileState.BUILT
    prr_file.watcher_deleted(wfp)
    assert prr_file.get_state(wfp) == FileState.PENDING
    assert step1.get_state(wfp) == StepState.PENDING
    assert step2.get_state(wfp) == StepState.PENDING


def test_watcher_updated(wfp):
    plan_key = "step:./plan.py"
    (tst_key,) = wfp.declare_static("root:", ["tst"])
    cat_key = wfp.define_step(plan_key, "cat tst", ["tst"])
    step1_key = wfp.define_step(plan_key, "bla1", out_paths=["prr"])
    step2_key = wfp.define_step(plan_key, "bla2", ["prr"])

    # Static
    cat = wfp.get_step(cat_key)
    cat.completed(wfp, True, StepHash(b"sfdsafds", b"zzz"))
    tst_file = wfp.get_file(tst_key)
    tst_file.watcher_deleted(wfp)
    assert tst_file.get_state(wfp) == FileState.MISSING
    assert cat.get_state(wfp) == StepState.PENDING
    tst_file.watcher_updated(wfp)
    assert tst_file.get_state(wfp) == FileState.STATIC
    assert cat.get_state(wfp) == StepState.PENDING

    # Built
    prr_key = "file:prr"
    prr_file = wfp.get_file(prr_key)
    assert prr_file.get_state(wfp) == FileState.PENDING
    prr_file.watcher_updated(wfp)
    assert prr_file.get_state(wfp) == FileState.PENDING
    step1 = wfp.get_step(step1_key)
    step1.completed(wfp, True, StepHash(b"11", b"zzz"))
    step2 = wfp.get_step(step2_key)
    step2.completed(wfp, False, StepHash(b"fail", b"inp_fail"))
    assert prr_file.get_state(wfp) == FileState.BUILT
    prr_file.watcher_updated(wfp)
    assert prr_file.get_state(wfp) == FileState.PENDING
    assert step1.get_state(wfp) == StepState.PENDING
    assert step2.get_state(wfp) == StepState.PENDING


def test_step_recycle(wfp):
    plan_key = "step:./plan.py"
    echo_key = wfp.define_step(plan_key, "echo foo > bar", out_paths=["bar"])
    echo1 = wfp.get_step(echo_key)
    step_hash = StepHash(b"bsfssfdsdfsdfasdfasa", b"zzz")
    echo1.completed(wfp, True, step_hash)
    assert isinstance(echo1.recording, StepRecording)
    wfp.orphan(echo_key)
    wfp.define_step(plan_key, "echo foo > bar", out_paths=["bar"])
    echo2 = wfp.get_step(echo_key)
    assert isinstance(echo2.recording, StepRecording)
    assert echo1 is not echo2
    assert echo1.hash.digest == echo2.hash.digest
    assert echo1.recording is echo2.recording


def test_dissolve(wfp):
    # Simulate use of pattern in and successful execution of plan.py.
    plan_key = "step:./plan.py"
    wfp.declare_static(plan_key, ["sub/"])
    wfp.declare_static(plan_key, ["sub/static.txt"])
    wfp.register_nglob(plan_key, NGlobMulti.from_patterns(["*.txt"]))
    assert wfp.job_queue.get_nowait() == RunJob(plan_key, None)
    assert wfp.job_queue.qsize() == 0
    plan = wfp.get_step(plan_key)
    plan.completed(wfp, True, StepHash(b"sth", b"zzz"))
    assert plan.get_state(wfp) == StepState.SUCCEEDED
    assert plan.hash.digest == b"sth"
    assert isinstance(plan.recording, StepRecording)

    # Check effect of anticipate_everthing_changed
    state = wfp.unstructure()
    wfp.dissolve()
    wfp.check_consistency()
    assert wfp.is_orphan(plan_key)
    assert wfp.is_orphan("file:./")
    assert wfp.is_orphan("file:plan.py")
    assert wfp.is_orphan("file:sub/")
    assert wfp.is_orphan("file:sub/static.txt")
    assert not wfp.is_orphan("root:")
    assert not wfp.is_orphan("vacuum:")
    assert plan.hash is None
    assert plan.recording is None

    # Reconstruct everything
    wfp.declare_static("root:", ["./", "plan.py"])
    wfp.define_step("root:", "./plan.py", ["plan.py"])
    wfp.declare_static(plan_key, ["sub/"])
    wfp.declare_static(plan_key, ["sub/static.txt"])
    wfp.register_nglob(plan_key, NGlobMulti.from_patterns(["*.txt"]))
    plan = wfp.get_step(plan_key)
    plan.completed(wfp, True, StepHash(b"sth", b"zzz"))
    assert wfp.unstructure() == state


def test_output_dir_nested(wfp):
    plan_key = "step:./plan.py"
    step_key = wfp.define_step(plan_key, "mkdir s/foo/bar/egg", out_paths=["s/foo/bar/egg/"])
    wfp.clean()
    assert "file:s/" in wfp.nodes
    assert "file:s/foo/" in wfp.nodes
    assert "file:s/foo/bar/" in wfp.nodes
    assert "file:s/foo/bar/egg/" in wfp.nodes
    assert wfp.is_orphan("file:s/")
    assert wfp.is_orphan("file:s/foo/")
    assert wfp.is_orphan("file:s/foo/bar/")
    assert wfp.get_creator("file:s/foo/bar/egg/") == "step:mkdir s/foo/bar/egg"
    wfp.orphan(step_key)
    wfp.clean()
    assert "file:s/" not in wfp.nodes


def test_clean_multipel_suppliers(wfp):
    plan_key = "step:./plan.py"
    (file_key,) = wfp.declare_static(plan_key, ["common.txt"])
    step1_key = wfp.define_step(
        plan_key, "prog1 common.txt", inp_paths=["common.txt"], out_paths=["output1.txt"]
    )
    step2_key = wfp.define_step(
        plan_key, "prog2 common.txt", inp_paths=["common.txt"], out_paths=["output2.txt"]
    )
    wfp.orphan(file_key)
    wfp.clean()
    assert file_key in wfp.nodes
    wfp.orphan(step1_key)
    wfp.clean()
    assert file_key in wfp.nodes
    wfp.orphan(step2_key)
    wfp.clean()
    assert file_key not in wfp.nodes


def test_env_vars(wfp):
    plan_key = "step:./plan.py"
    step_key = wfp.define_step(plan_key, "prog1", env_vars=["name", "other"])
    step = wfp.get_step(step_key)
    assert step.initial_env_vars == {"name", "other"}
    assert step.amended_env_vars == set()
    check_workflow_unstructure(wfp)


def test_amended_env_vars(wfp):
    plan_key = "step:./plan.py"
    step_key = wfp.define_step(plan_key, "prog1", env_vars=["egg"])
    step = wfp.get_step(step_key)
    assert step.get_state(wfp) == StepState.QUEUED
    assert wfp.job_queue.get_nowait() == RunJob(plan_key, None)
    assert wfp.job_queue.get_nowait() == RunJob(step_key, None)
    wfp.amend_step(step_key, env_vars=["foo", "egg"])
    wfp.amend_step(step_key, env_vars=["foo", "bar"])
    assert step.initial_env_vars == {"egg"}
    assert step.amended_env_vars == {"foo", "bar"}
    check_workflow_unstructure(wfp)


def test_acyclic_amend_static(wfp):
    plan_key = "step:./plan.py"
    plan = wfp.get_step(plan_key)
    assert plan.get_state(wfp) == StepState.QUEUED
    assert wfp.job_queue.get_nowait() == RunJob(plan_key, None)
    wfp.declare_static(plan_key, ["static.txt"])
    wfp.amend_step(plan_key, inp_paths=["static.txt"])
    assert plan.get_inp_paths(wfp) == ["./", "plan.py", "static.txt"]
    assert plan.get_static_paths(wfp) == ["static.txt"]


def test_cyclic_two_steps(wfp):
    plan_key = "step:./plan.py"
    wfp.define_step(plan_key, "cat first > second", inp_paths=["first"], out_paths=["second"])
    with pytest.raises(GraphError):
        wfp.define_step(plan_key, "cat second > first", inp_paths=["second"], out_paths=["first"])
    assert wfp.is_orphan("file:first")


def test_optional_imply(wfp):
    # Define sequence of steps: optional -> mandatory
    plan_key = "step:./plan.py"
    step1_key = wfp.define_step(plan_key, "prog1", out_paths=["foo"], optional=True)
    step1 = wfp.get_step(step1_key)
    assert step1.get_mandatory(wfp) == Mandatory.NO
    assert step1.get_state(wfp) == StepState.PENDING
    step2_key = wfp.define_step(plan_key, "prog2", inp_paths=["foo"])
    step2 = wfp.get_step(step2_key)
    assert step2.get_mandatory(wfp) == Mandatory.YES
    assert step2.get_state(wfp) == StepState.PENDING
    assert step1.get_mandatory(wfp) == Mandatory.IMPLIED
    assert step1.get_state(wfp) == StepState.QUEUED

    # Simulate scheduler
    assert wfp.job_queue.get_nowait() == RunJob(plan_key, None)
    assert wfp.job_queue.get_nowait() == RunJob(step1_key, None)
    assert wfp.job_queue.qsize() == 0
    step1.completed(wfp, True, StepHash(b"sth", b"zzz"))
    assert step2.get_state(wfp) == StepState.QUEUED
    assert wfp.job_queue.get_nowait() == RunJob(step2_key, None)

    # Simulate watcher: orphan mandatory step
    wfp.orphan(step2_key)
    assert step1.get_mandatory(wfp) == Mandatory.NO
    assert step1.get_state(wfp) == StepState.SUCCEEDED

    # Run clean
    wfp.clean()
    assert step2_key not in wfp.nodes
    assert step2_key not in wfp.step_mandatory


def test_optional_imply_chain(wfp):
    # Define sequence of steps: optional -> optional -> mandatory
    plan_key = "step:./plan.py"
    step1_key = wfp.define_step(plan_key, "prog1", out_paths=["foo"], optional=True)
    step1 = wfp.get_step(step1_key)
    assert step1.get_mandatory(wfp) == Mandatory.NO
    assert step1.get_state(wfp) == StepState.PENDING
    step2_key = wfp.define_step(
        plan_key, "prog2", inp_paths=["foo"], out_paths=["bar"], optional=True
    )
    step2 = wfp.get_step(step2_key)
    assert step2.get_mandatory(wfp) == Mandatory.NO
    assert step2.get_state(wfp) == StepState.PENDING
    step3_key = wfp.define_step(plan_key, "prog3", inp_paths=["bar"])
    step3 = wfp.get_step(step3_key)
    assert step3.get_mandatory(wfp) == Mandatory.YES
    assert step3.get_state(wfp) == StepState.PENDING
    assert step2.get_mandatory(wfp) == Mandatory.IMPLIED
    assert step2.get_state(wfp) == StepState.PENDING
    assert step1.get_mandatory(wfp) == Mandatory.IMPLIED
    assert step1.get_state(wfp) == StepState.QUEUED

    # Simulate scheduler
    assert wfp.job_queue.get_nowait() == RunJob(plan_key, None)
    assert wfp.job_queue.get_nowait() == RunJob(step1_key, None)
    assert wfp.job_queue.qsize() == 0
    step1.completed(wfp, True, StepHash(b"sth", b"zzz"))
    assert step2.get_state(wfp) == StepState.QUEUED
    assert wfp.job_queue.get_nowait() == RunJob(step2_key, None)
    step2.completed(wfp, True, StepHash(b"sth", b"zzz"))
    assert step3.get_state(wfp) == StepState.QUEUED
    assert wfp.job_queue.get_nowait() == RunJob(step3_key, None)
    step3.completed(wfp, True, StepHash(b"sth", b"zzz"))

    # Simulate watcher: orphan middle step
    wfp.orphan(step2_key)
    assert step1.get_mandatory(wfp) == Mandatory.NO
    assert step1.get_state(wfp) == StepState.SUCCEEDED
    assert step2.get_mandatory(wfp) == Mandatory.IMPLIED
    assert step2.get_state(wfp) == StepState.SUCCEEDED
    assert step3.get_mandatory(wfp) == Mandatory.YES
    assert step3.get_state(wfp) == StepState.PENDING


def test_optional_infer(wfp):
    # Define sequence of steps: optional -> mandatory
    plan_key = "step:./plan.py"
    step1_key = wfp.define_step(plan_key, "prog1", inp_paths=["foo"])
    step1 = wfp.get_step(step1_key)
    assert step1.get_mandatory(wfp) == Mandatory.YES
    assert step1.get_state(wfp) == StepState.PENDING
    step2_key = wfp.define_step(plan_key, "prog2", out_paths=["foo"], optional=True)
    step2 = wfp.get_step(step2_key)
    assert step2.get_mandatory(wfp) == Mandatory.IMPLIED
    assert step2.get_state(wfp) == StepState.QUEUED

    # Add third optional step and clean: Mandatory.NO steps are orphaned upon cleaning
    step3_key = wfp.define_step(plan_key, "prog3", out_paths=["bar"], optional=True)
    step3 = wfp.get_step(step3_key)
    assert step3.get_mandatory(wfp) == Mandatory.NO
    assert step3.get_state(wfp) == StepState.PENDING
    wfp.clean()
    assert step3_key not in wfp.nodes


def test_optional_infer_chained(wfp):
    plan_key = "step:./plan.py"
    step1_key = wfp.define_step(plan_key, "prog1", inp_paths=["foo"])
    step1 = wfp.get_step(step1_key)
    assert step1.get_mandatory(wfp) == Mandatory.YES
    assert step1.get_state(wfp) == StepState.PENDING
    step2_key = wfp.define_step(plan_key, "prog2", out_paths=["bar"], optional=True)
    step2 = wfp.get_step(step2_key)
    assert step2.get_mandatory(wfp) == Mandatory.NO
    assert step2.get_state(wfp) == StepState.PENDING
    step3_key = wfp.define_step(
        plan_key, "prog3", inp_paths=["bar"], out_paths=["foo"], optional=True
    )
    step3 = wfp.get_step(step3_key)
    assert step3.get_mandatory(wfp) == Mandatory.IMPLIED
    assert step3.get_state(wfp) == StepState.PENDING
    assert step2.get_mandatory(wfp) == Mandatory.IMPLIED
    assert step2.get_state(wfp) == StepState.QUEUED
    assert step1.get_mandatory(wfp) == Mandatory.YES
    assert step1.get_state(wfp) == StepState.PENDING


def test_deferred_glob_basic(wfp):
    plan_key = "step:./plan.py"
    # Define a step with an orphan input
    wfp.define_step(plan_key, "cat head_1.txt", inp_paths=["head_1.txt"])

    # Define deferred glob and check attributes
    patterns1 = ["head_${*char}.txt", "tail_${*char}.txt"]
    with pytest.raises(GraphError):
        wfp.defer_glob(plan_key, patterns1)
    patterns2 = ["head_*.txt", "tail_*.txt"]
    dg_key = wfp.defer_glob(plan_key, patterns2)
    assert dg_key == "dg:'head_*.txt' 'tail_*.txt'"
    dg = wfp.get_deferred_glob(dg_key)
    assert isinstance(dg, DeferredGlob)

    # Check if head_1.txt is static
    assert wfp.get_file("file:head_1.txt").get_state(wfp) == FileState.STATIC
    assert "head_1.txt" in dg.ngm.files()

    # Use deferred glob after it is added
    wfp.define_step(plan_key, "cat tail_1.txt", inp_paths=["tail_1.txt"])
    assert wfp.get_file("file:tail_1.txt").get_state(wfp) == FileState.STATIC
    assert "tail_1.txt" in dg.ngm.files()


def test_deferred_glob_clean(wfp):
    plan_key = "step:./plan.py"
    dg_key = wfp.defer_glob(plan_key, ["static/**"])
    step_key = wfp.define_step(plan_key, "cat static/foo/bar.txt", inp_paths=["static/foo/bar.txt"])

    # Check effect of defining the step on the deferred_glob
    dg = wfp.get_deferred_glob(dg_key)
    assert wfp.get_file("file:static/").get_state(wfp) == FileState.STATIC
    assert wfp.get_file("file:static/foo/").get_state(wfp) == FileState.STATIC
    assert wfp.get_file("file:static/foo/bar.txt").get_state(wfp) == FileState.STATIC
    assert "static/" in dg.ngm.files()
    assert "static/foo/" in dg.ngm.files()
    assert "static/foo/bar.txt" in dg.ngm.files()

    # Simulate the execution of the steps
    assert wfp.job_queue.get_nowait() == RunJob(plan_key, None)
    plan = wfp.get_step(plan_key)
    plan.completed(wfp, True, StepHash(b"sth", b"zzz"))
    assert wfp.job_queue.get_nowait() == RunJob(step_key, None)
    assert wfp.job_queue.qsize() == 0
    step = wfp.get_step(step_key)
    step.completed(wfp, True, StepHash(b"sth", b"zzz"))
    check_workflow_unstructure(wfp)

    # Check the recording
    assert plan.recording.deferred_glob_args == [["static/**"]]
    assert step.recording.deferred_glob_args == []

    # Orphan the step, clean and check result
    wfp.orphan(step_key)
    wfp.clean()
    assert dg_key in wfp.nodes
    assert step_key not in wfp.nodes
    assert "file:static/" not in wfp.nodes
    assert "file:static/foo/" not in wfp.nodes
    assert "file:static/foo/bar.txt" not in wfp.nodes
    assert "static/" not in dg.ngm.files()
    assert "static/foo/" not in dg.ngm.files()
    assert "static/foo/bar.txt" not in dg.ngm.files()

    # make the plan pending and check recording
    plan.make_pending(wfp)
    assert wfp.is_orphan(dg_key)
    plan.queue_if_appropriate(wfp)
    assert plan.get_state(wfp) == StepState.QUEUED
    assert wfp.job_queue.qsize() == 1
    assert dg_key in wfp.nodes


def test_deferred_glob_two_matches(wfp):
    plan_key = "step:./plan.py"
    wfp.defer_glob(plan_key, ["*.md"])
    wfp.defer_glob(plan_key, ["README.*"])
    with pytest.raises(GraphError):
        wfp.define_step(plan_key, "cat README.md", inp_paths=["README.md"])


def test_deferred_glob_static(wfp):
    plan_key = "step:./plan.py"
    wfp.defer_glob(plan_key, ["*.md"])
    with pytest.raises(GraphError):
        wfp.declare_static(plan_key, ["README.md"])


def test_deferred_glob_output(wfp):
    plan_key = "step:./plan.py"
    wfp.defer_glob(plan_key, ["*.md"])
    with pytest.raises(GraphError):
        wfp.define_step(plan_key, "echo foo > README.md", out_paths=["README.md"])


def test_deferred_glob_volatile(wfp):
    plan_key = "step:./plan.py"
    wfp.defer_glob(plan_key, ["*.md"])
    with pytest.raises(GraphError):
        wfp.define_step(plan_key, "echo foo > README.md", vol_paths=["README.md"])


def test_define_step_reqdir_out_path(wfp):
    wfp.define_step("step:./plan.py", "echo", out_paths=["sub/dir/out"])
    reqdir_key = "file:sub/dir/"
    assert wfp.is_orphan(reqdir_key)
    reqdir = wfp.get_file(reqdir_key)
    assert reqdir.get_state(wfp) == FileState.PENDING


def test_define_step_reqdir_vol_path(wfp):
    wfp.define_step("step:./plan.py", "echo", vol_paths=["sub/dir/vol"])
    reqdir_key = "file:sub/dir/"
    assert wfp.is_orphan(reqdir_key)
    reqdir = wfp.get_file(reqdir_key)
    assert reqdir.get_state(wfp) == FileState.PENDING


def test_define_step_reqdir_workdir(wfp):
    wfp.define_step("step:./plan.py", "echo", workdir="sub/dir/")
    reqdir_key = "file:sub/dir/"
    assert wfp.is_orphan(reqdir_key)
    reqdir = wfp.get_file(reqdir_key)
    assert reqdir.get_state(wfp) == FileState.PENDING


def test_amend_step_reqdir_out_path(wfp):
    step_key = wfp.define_step("step:./plan.py", "echo")
    wfp.amend_step(step_key, out_paths=["sub/dir/out"])
    reqdir_key = "file:sub/dir/"
    assert wfp.is_orphan(reqdir_key)
    reqdir = wfp.get_file(reqdir_key)
    assert reqdir.get_state(wfp) == FileState.PENDING


def test_amend_step_reqdir_vol_path(wfp):
    step_key = wfp.define_step("step:./plan.py", "echo")
    wfp.amend_step(step_key, vol_paths=["sub/dir/vol"])
    reqdir_key = "file:sub/dir/"
    assert wfp.is_orphan(reqdir_key)
    reqdir = wfp.get_file(reqdir_key)
    assert reqdir.get_state(wfp) == FileState.PENDING


def test_get_inp_paths(wfp):
    step_key = wfp.define_step("step:./plan.py", "script", inp_paths=["foo"])
    step = wfp.get_step(step_key)
    assert step.get_inp_paths(wfp) == ["./", "foo"]
    assert step.get_inp_paths(wfp, orphan=True) == [["./", False], ["foo", True]]
    assert step.get_inp_paths(wfp, state=True) == [
        ["./", FileState.STATIC],
        ["foo", FileState.PENDING],
    ]
    assert step.get_inp_paths(wfp, state=True, orphan=True) == [
        ["./", FileState.STATIC, False],
        ["foo", FileState.PENDING, True],
    ]
    assert step.get_inp_paths(wfp, file_hash=True) == [
        ["./", FileHash(b"u", 0, 0.0, 0, 0)],
        ["foo", FileHash(b"u", 0, 0.0, 0, 0)],
    ]


def test_get_out_paths(wfp):
    step_key = wfp.define_step("step:./plan.py", "script", out_paths=["foo", "bar"])
    step = wfp.get_step(step_key)
    wfp.get_file("file:bar").set_state(wfp, FileState.BUILT)
    assert step.get_out_paths(wfp) == ["bar", "foo"]
    assert step.get_out_paths(wfp, state=True) == [
        ["bar", FileState.BUILT],
        ["foo", FileState.PENDING],
    ]
    assert step.get_out_paths(wfp, file_hash=True) == [
        ["bar", FileHash(b"u", 0, 0.0, 0, 0)],
        ["foo", FileHash(b"u", 0, 0.0, 0, 0)],
    ]
    assert step.get_out_paths(wfp, state=True, file_hash=True) == [
        ["bar", FileState.BUILT, FileHash(b"u", 0, 0.0, 0, 0)],
        ["foo", FileState.PENDING, FileHash(b"u", 0, 0.0, 0, 0)],
    ]


def test_get_vol_paths(wfp):
    step_key = wfp.define_step("step:./plan.py", "script", vol_paths=["foo", "bar"])
    step = wfp.get_step(step_key)
    assert step.get_vol_paths(wfp) == ["bar", "foo"]
    assert step.get_vol_paths(wfp, file_hash=True) == [
        ["bar", FileHash(b"u", 0, 0.0, 0, 0)],
        ["foo", FileHash(b"u", 0, 0.0, 0, 0)],
    ]


def test_get_static_paths(wfp):
    step_key = wfp.define_step("step:./plan.py", "script")
    wfp.declare_static(step_key, ["foo", "bar"])
    step = wfp.get_step(step_key)
    wfp.get_file("file:bar").set_state(wfp, FileState.MISSING)
    assert step.get_static_paths(wfp) == ["bar", "foo"]
    assert step.get_static_paths(wfp, state=True) == [
        ["bar", FileState.MISSING],
        ["foo", FileState.STATIC],
    ]
    assert step.get_static_paths(wfp, file_hash=True) == [
        ["bar", FileHash(b"u", 0, 0.0, 0, 0)],
        ["foo", FileHash(b"u", 0, 0.0, 0, 0)],
    ]
    assert step.get_static_paths(wfp, state=True, file_hash=True) == [
        ["bar", FileState.MISSING, FileHash(b"u", 0, 0.0, 0, 0)],
        ["foo", FileState.STATIC, FileHash(b"u", 0, 0.0, 0, 0)],
    ]


def test_replay_amend_orphan_inputs(wfp):
    plan_key = "step:./plan.py"
    step_key = wfp.define_step(plan_key, "echo foo > bar", inp_paths=["foo"], out_paths=["bar"])
    (foo_key,) = wfp.declare_static(plan_key, ["foo"])

    # Simulate running the step
    assert wfp.job_queue.get_nowait() == RunJob(plan_key, None)
    assert wfp.job_queue.get_nowait() == RunJob(step_key, None)
    step = wfp.get_step(step_key)
    wfp.amend_step(step_key, env_vars=["AAA"], vol_paths=["bbb"])
    step.completed(wfp, True, StepHash(b"step_ok", b"zzz"))
    assert step.get_state(wfp) == StepState.SUCCEEDED

    # Make the static input orphan and redefine the step
    wfp.orphan(foo_key)
    assert step.get_state(wfp) == StepState.PENDING
    wfp.orphan(step_key)
    wfp.define_step(plan_key, "echo foo > bar", inp_paths=["foo"], out_paths=["bar"])
    step = wfp.get_step(step_key)

    # Check that amended info is back and recording is still in place
    assert step.hash is not None
    assert isinstance(step.recording, StepRecording)
    assert step.get_inp_paths(wfp) == ["./", "foo"]
    assert step.get_out_paths(wfp) == ["bar"]
    assert step.get_vol_paths(wfp) == ["bbb"]


def test_define_step_out_nested(wfp):
    plan_key = "step:./plan.py"
    step_key = wfp.define_step(plan_key, "script", out_paths=["sub/", "sub/foo/", "sub/foo/bar"])
    step = wfp.get_step(step_key)
    assert step.get_inp_paths(wfp) == ["./"]
    assert step.get_out_paths(wfp) == ["sub/", "sub/foo/", "sub/foo/bar"]


def test_define_step_vol_nested(wfp):
    plan_key = "step:./plan.py"
    step_key = wfp.define_step(
        plan_key, "script", out_paths=["sub/", "sub/foo/"], vol_paths=["sub/foo/bar"]
    )
    step = wfp.get_step(step_key)
    assert step.get_inp_paths(wfp) == ["./"]
    assert step.get_out_paths(wfp) == ["sub/", "sub/foo/"]
    assert step.get_vol_paths(wfp) == ["sub/foo/bar"]


def test_amend_step_out_nested(wfp):
    plan_key = "step:./plan.py"
    step_key = wfp.define_step(plan_key, "script", out_paths=["sub/"])
    wfp.amend_step(step_key, out_paths=["sub/foo/", "sub/foo/bar"])
    step = wfp.get_step(step_key)
    assert step.get_inp_paths(wfp) == ["./"]
    assert step.get_out_paths(wfp) == ["sub/", "sub/foo/", "sub/foo/bar"]


def test_amend_step_vol_nested(wfp):
    plan_key = "step:./plan.py"
    step_key = wfp.define_step(plan_key, "script", out_paths=["sub/"])
    wfp.amend_step(step_key, out_paths=["sub/foo/"], vol_paths=["sub/foo/bar"])
    step = wfp.get_step(step_key)
    assert step.get_inp_paths(wfp) == ["./"]
    assert step.get_out_paths(wfp) == ["sub/", "sub/foo/"]
    assert step.get_vol_paths(wfp) == ["sub/foo/bar"]
