# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Unit tests for stepup.core.workflow."""

import asyncio
import contextlib
import hashlib
import re
import sqlite3
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from conftest import amend_step, declare_static, fake_hash
from path import Path

from stepup.core.constants import STEPUP_DIR
from stepup.core.enums import FileState, HashUpdateCause, Need, StepState
from stepup.core.exceptions import GraphError
from stepup.core.file import File
from stepup.core.hash import FileHash, StepHash
from stepup.core.nglob import NamedGlob
from stepup.core.outcome import ChildOutcome
from stepup.core.path import dir_range_upper
from stepup.core.sqlite3 import DBSession
from stepup.core.static_tree import StaticTree
from stepup.core.step import Step
from stepup.core.stepinfo import StepInfo
from stepup.core.workflow import (
    UNCONFIRMED_INPUTS,
    GlobViolation,
    Workflow,
    _static_tree_file_message,
    _static_tree_product_message,
)


def _amend(wfx: Workflow, step: Step, **kwargs) -> tuple[bool, list]:
    """Amend a step and collapse the result into (carry_on, to_check).

    Mirrors `DirectorHandler.handle_amend` in `stepup/core/director.py`.
    """
    is_detached, unavailable, unfresh, to_check = amend_step(wfx, step, **kwargs)
    carry_on = not is_detached and not unavailable and not unfresh
    return carry_on, to_check


TEST_FILE_GRAPH = """\
root:
             product   file:script.sh

file:script.sh
               state = STATIC
              digest = 116b4e2b ac3e35be fdfae3f3 b6eb8891 1689c30b fe6696d9 e6b0139d a8cb5e72
             creator   root:
"""


async def test_file(wfs: Workflow):
    async with wfs.db:
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


async def test_invalid_path(wfs):
    async with wfs.db:
        with pytest.raises(ValueError):
            declare_static(wfs, wfs.root, [""])
        with pytest.raises(ValueError):
            declare_static(wfs, wfs.root, ["."])
        with pytest.raises(ValueError):
            declare_static(wfs, wfs.root, ["foo/."])
        with pytest.raises(ValueError):
            declare_static(wfs, wfs.root, ["foo/bar/.."])


async def test_declare_file_rejects_missing(wfs: Workflow):
    """`_declare_file` must reject `MISSING`: callers must go through `UNCONFIRMED` first."""
    async with wfs.db:
        with pytest.raises(ValueError):
            wfs._declare_file(wfs.root, "foo.txt", FileState.MISSING)


TEST_STEP_GRAPH = """\
root:
             product   step:cp foo.txt sub/bar.txt

step:cp foo.txt sub/bar.txt
               state = PENDING
                need = DEFAULT
             creator   root:
              source   (file:foo.txt)
             product   file:sub/bar.txt
                sink   file:sub/bar.txt

(file:foo.txt)
               state = AWAITED
                sink   step:cp foo.txt sub/bar.txt

file:sub/bar.txt
               state = AWAITED
             creator   step:cp foo.txt sub/bar.txt
              source   step:cp foo.txt sub/bar.txt
"""


TEST_STEP_GRAPH2 = """\
root:
             product   step:cp foo.txt sub/bar.txt

step:cp foo.txt sub/bar.txt
               state = RUNNING
                need = DEFAULT
             creator   root:
              source   (file:foo.txt)
              source   (file:spam.txt) [dynamic]
             product   file:egg.csv
             product   file:sub/bar.txt
                sink   file:egg.csv [dynamic]
                sink   file:sub/bar.txt

(file:foo.txt)
               state = AWAITED
                sink   step:cp foo.txt sub/bar.txt

file:sub/bar.txt
               state = AWAITED
             creator   step:cp foo.txt sub/bar.txt
              source   step:cp foo.txt sub/bar.txt

(file:spam.txt)
               state = AWAITED
                sink   step:cp foo.txt sub/bar.txt

file:egg.csv
               state = AWAITED
             creator   step:cp foo.txt sub/bar.txt
              source   step:cp foo.txt sub/bar.txt
"""


async def test_step(wfs: Workflow):
    # Normal case
    async with wfs.db:
        to_check = wfs.define_step(
            wfs.root, "cp foo.txt sub/bar.txt", inp_paths=["foo.txt"], out_paths=["sub/bar.txt"]
        )
        assert to_check == {}
        step = wfs.find(Step, "cp foo.txt sub/bar.txt")
        assert step.key() == "step:cp foo.txt sub/bar.txt"
        command, workdir = step.command_and_workdir
        assert command == "cp foo.txt sub/bar.txt"
        assert workdir == Path(".")
        assert isinstance(workdir, Path)
        assert wfs.format_str() == TEST_STEP_GRAPH
        assert list(wfs.nodes(Step)) == [step]
        assert {(r.path, r.detached) for r in step.inp_paths(include_detached=True)} == {
            ("foo.txt", True)
        }
        assert {r.path for r in step.out_paths()} == {"sub/bar.txt"}

    # Redefining the boot script is not allowed.
    with pytest.raises(GraphError):
        async with wfs.db:
            wfs.define_step(
                wfs.root, "cp foo.txt sub/bar.txt", inp_paths=["foo.txt"], out_paths=["sub/bar.txt"]
            )
    async with wfs.db:
        assert wfs.format_str() == TEST_STEP_GRAPH

    # Make the step RUNNING and test amending stuff.
    # (The extra inputs and outputs are not meant to be sensible for the copy command.)
    async with wfs.db:
        step.set_state(StepState.RUNNING)
        assert not step.has_unavailable_dynamic_input()
    async with wfs.db:
        is_detached, unavailable, unfresh, to_check = amend_step(
            wfs, step, inp_paths=["spam.txt"], out_paths=["egg.csv"]
        )
        assert to_check == {}
        assert not is_detached
        assert unavailable == {"spam.txt"}
        assert not unfresh
        assert step.has_unavailable_dynamic_input()
        assert {(r.path, r.detached) for r in step.inp_paths(include_detached=True)} == {
            ("foo.txt", True),
            ("spam.txt", True),
        }
        assert {r.path for r in step.out_paths()} == {"egg.csv", "sub/bar.txt"}
        assert wfs.format_str() == TEST_STEP_GRAPH2

    # Amend an input that was already known, which just gets ignored.
    async with wfs.db:
        amend_step(wfs, step, inp_paths=["foo.txt"])
        assert wfs.format_str() == TEST_STEP_GRAPH2

    # Try a few things that should raise errors
    with pytest.raises(GraphError):
        async with wfs.db:
            # Amend an output that was already known.
            amend_step(wfs, step, out_paths=["egg.csv"])
    async with wfs.db:
        assert wfs.format_str() == TEST_STEP_GRAPH2
    with pytest.raises(GraphError):
        async with wfs.db:
            # Amend a new input and an output that was already known.
            amend_step(wfs, step, inp_paths=["new.zip"], out_paths=["egg.csv"])
    async with wfs.db:
        assert wfs.format_str() == TEST_STEP_GRAPH2


TEST_SIMPLE_EXAMPLE_GRAPH1 = """\
root:
             product   step:cp foo.txt bar.txt

step:cp foo.txt bar.txt
               state = PENDING
                need = DEFAULT
             creator   root:
              source   (file:foo.txt)
             product   file:bar.txt
                sink   file:bar.txt

(file:foo.txt)
               state = AWAITED
                sink   step:cp foo.txt bar.txt

file:bar.txt
               state = AWAITED
             creator   step:cp foo.txt bar.txt
              source   step:cp foo.txt bar.txt
"""

TEST_SIMPLE_EXAMPLE_GRAPH2 = """\
root:
             product   file:foo.txt
             product   step:cp foo.txt bar.txt

step:cp foo.txt bar.txt
               state = PENDING
                need = DEFAULT
             creator   root:
              source   file:foo.txt
             product   file:bar.txt
                sink   file:bar.txt

file:foo.txt
               state = STATIC
              digest = ddab29ff 2c393ee5 2855d21a 240eb05f 775df88e 3ce347df 759f0c4b 80356c35
             creator   root:
                sink   step:cp foo.txt bar.txt

file:bar.txt
               state = AWAITED
             creator   step:cp foo.txt bar.txt
              source   step:cp foo.txt bar.txt
"""

TEST_SIMPLE_EXAMPLE_GRAPH3 = """\
root:
             product   file:foo.txt
             product   step:cp foo.txt bar.txt

step:cp foo.txt bar.txt
               state = SUCCEEDED
                need = DEFAULT
          inp_digest = fa0cc090 b1be8b9f c51d9037 4d828d82 bef5b405 d25b7eae 565b0e1b 683c8cfc
          out_digest = 989a8ef2 4a8ea52e 844a0770 1bfae079 4a7088e1 6a2ba779 3dfacd9a f1164aa1
           explained = yes
             creator   root:
              source   file:foo.txt
             product   file:bar.txt
                sink   file:bar.txt

file:foo.txt
               state = STATIC
              digest = ddab29ff 2c393ee5 2855d21a 240eb05f 775df88e 3ce347df 759f0c4b 80356c35
             creator   root:
                sink   step:cp foo.txt bar.txt

file:bar.txt
               state = BUILT
              digest = 08bd2d24 7cc7aa38 b8c4b7fd 20ee7eda d0b593c3 debce92f 595c9d01 6da40bae
             creator   step:cp foo.txt bar.txt
              source   step:cp foo.txt bar.txt
"""

TEST_SIMPLE_EXAMPLE_GRAPH4 = """\
root:
             product   file:foo.txt
             product   step:cp foo.txt bar.txt

step:cp foo.txt bar.txt
               state = PENDING
                need = DEFAULT
          inp_digest = fa0cc090 b1be8b9f c51d9037 4d828d82 bef5b405 d25b7eae 565b0e1b 683c8cfc
          out_digest = 989a8ef2 4a8ea52e 844a0770 1bfae079 4a7088e1 6a2ba779 3dfacd9a f1164aa1
           explained = yes
             creator   root:
              source   file:foo.txt
             product   file:bar.txt
                sink   file:bar.txt

file:foo.txt
               state = STATIC
              digest = ddab29ff 2c393ee5 2855d21a 240eb05f 775df88e 3ce347df 759f0c4b 80356c35
             creator   root:
                sink   step:cp foo.txt bar.txt

file:bar.txt
               state = OUTDATED
              digest = 08bd2d24 7cc7aa38 b8c4b7fd 20ee7eda d0b593c3 debce92f 595c9d01 6da40bae
             creator   step:cp foo.txt bar.txt
              source   step:cp foo.txt bar.txt
"""


async def test_simple_example(wfs: Workflow):
    async with wfs.db:
        # Create a runnable step and check it
        to_check = wfs.define_step(
            wfs.root, "cp foo.txt bar.txt", inp_paths=["foo.txt"], out_paths=["bar.txt"]
        )
        assert to_check == {}
        assert wfs.format_str() == TEST_SIMPLE_EXAMPLE_GRAPH1
        step = wfs.find(Step, "cp foo.txt bar.txt")

        # Declare the static input and check graph
        foo = declare_static(wfs, wfs.root, ["foo.txt"])[0]
        assert wfs.format_str() == TEST_SIMPLE_EXAMPLE_GRAPH2
        assert wfs.count_required_steps() == (0, 1)

    # Verify things that should not be allowed
    with pytest.raises(GraphError):
        async with wfs.db:
            declare_static(wfs, wfs.root, ["bar.txt"])

    # Simulate the builder, pretending to execute the step
    async with wfs.db:
        out_hashes = {"bar.txt": fake_hash("bar.txt")}
        wfs.update_file_hashes(out_hashes, cause=HashUpdateCause.SUCCEEDED)
        inp_hashes = {"foo.txt": foo.get_hash()}
        env_values = {"A": "B"}
        step_hash = StepHash.from_inp(step.key(), True, inp_hashes, env_values)
        step_hash = step_hash.evolve_out(out_hashes)
        step.mark_completed(step_hash, False)
        assert wfs.format_str() == TEST_SIMPLE_EXAMPLE_GRAPH3
        assert wfs.count_required_steps() == (1, 1)

    # Check hashes
    async with wfs.db:
        step_hash2 = step.get_hash()
    assert step_hash2.inp_info.inp_hashes == dict(inp_hashes)
    assert step_hash2.inp_info.env_values == env_values
    assert step_hash2.out_info.out_hashes == dict(out_hashes)

    # Re-declaring foo.txt as static is now a no-op: same creator, same file.
    async with wfs.db:
        assert wfs.declare_static_files(wfs.root, ["foo.txt"]) == {}
        assert wfs.find(File, "foo.txt").get_state() == FileState.STATIC
    # bar.txt is a build product of another creator, so declaring it static still raises.
    with pytest.raises(GraphError):
        async with wfs.db:
            declare_static(wfs, wfs.root, ["bar.txt"])

    async with wfs.db:
        # simulate a change in the input file
        wfs.update_file_hashes({"foo.txt": fake_hash("foo.txt")}, cause=HashUpdateCause.EXTERNAL)
        assert wfs.format_str() == TEST_SIMPLE_EXAMPLE_GRAPH4
        assert step.get_state() == StepState.PENDING

        # simulate a skip
        step.mark_completed(step_hash, False)
        assert wfs.format_str() == TEST_SIMPLE_EXAMPLE_GRAPH3
        assert step.get_state() == StepState.SUCCEEDED


async def test_define_boot_input_static(wfs: Workflow):
    async with wfs.db:
        to_check = wfs.define_step(wfs.root, "echo", inp_paths=["foo.txt"])
        assert to_check == {}
        echo = wfs.find(Step, "echo")
        declare_static(wfs, wfs.root, ["foo.txt"])
        foo = wfs.find(File, "foo.txt")
        assert echo.creator() is not None
        assert list(foo.sinks()) == [echo]
        assert list(foo.sources()) == []
        assert list(echo.sinks()) == []
        assert set(echo.sources()) == {foo}


async def test_command_and_workdir_string(wfs: Workflow):
    with pytest.raises(ValueError):
        async with wfs.db:
            wfs.define_step(wfs.root, "echo  # wd=foo")


async def test_define_boot_static_input(wfs: Workflow):
    async with wfs.db:
        (foo,) = declare_static(wfs, wfs.root, ["foo.txt"])
        to_check = wfs.define_step(wfs.root, "echo", inp_paths=["foo.txt"])
        assert to_check == {}
        echo = wfs.find(Step, "echo")
        assert echo.creator().i is not None
        assert list(foo.sinks()) == [echo]
        assert list(foo.sources()) == []
        assert list(echo.sinks()) == []
        assert set(echo.sources()) == {foo}


async def test_redefine_boot(wfs: Workflow):
    async with wfs.db:
        to_check = wfs.define_step(wfs.root, "echo 1")
        assert to_check == {}
        step = wfs.find(Step, "echo 1")
    with pytest.raises(GraphError):
        async with wfs.db:
            wfs.define_step(wfs.root, "echo 2")
    async with wfs.db:
        step.detach()
        wfs.define_step(wfs.root, "echo 3")


async def test_define_boot_input_detached(wfs: Workflow):
    async with wfs.db:
        wfs.define_step(wfs.root, "echo", inp_paths=["foo.txt"])
        foo = wfs.find(File, "foo.txt")
        assert isinstance(foo, File)
        foo, detached = wfs.find_and_detached(File, "foo.txt")
        assert detached
        assert foo.is_detached()


async def test_redefine_step(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        to_check = wfp.define_step(plan, "echo")
        assert to_check == {}
        echo = wfp.find(Step, "echo")
        assert not echo.is_detached()
        assert echo.get_state() == StepState.PENDING
        assert list(wfp.nodes(Step)) == [plan, echo]
    with pytest.raises(GraphError):
        async with wfp.db:
            wfp.define_step(plan, "echo")
    async with wfp.db:
        echo.detach()
        assert echo.is_detached()
        wfp.define_step(plan, "echo")
        assert not echo.is_detached()
        assert echo.get_state() == StepState.PENDING


async def test_rerun_creator_detaches_running_child(wfp: Workflow):
    """A detached, still-`RUNNING` step's RPC calls must be harmless no-ops, not crash it.

    This models a race that can occur in a real build: a step (`plan`) creates a
    child step (`sub`), e.g. via a `step()` call in a `plan.py` script:

    - StepUp is interrupted early, such that `sub` does not complete.
    - The user then modifies `plan.py`, resulting in a slightly different child step.
    - StepUp is restarted, which causes `plan` to be rerun.
    - When `plan` reruns, it detaches all its product steps, but by that time `sub`
      may already have started running again in the executor.
    - `sub`'s (still running, but doomed) child process may still call `amend()`, e.g.
      via `getenv()`, before its own command terminates on its own.

    `amend_step` silently no-ops instead of raising, so a stray RPC call from `sub`'s
    still-alive child does not crash anything; `sub` itself keeps running until its
    command terminates, at which point `Step.mark_completed()`'s `is_detached()` branch
    discovers it and reports it as `DETACHED` (see `Executor.report()`).
    """
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "sub")
        sub = wfp.find(Step, "sub")
        assert not sub.is_detached()

        # The scheduler dispatches `sub`: it is forked and starts running.
        sub.set_state(StepState.RUNNING)

        # `plan` succeeds, but is later marked pending again, e.g. because a new file
        # matching one of its `glob()` patterns was confirmed while `sub` is still running.
        plan.set_state(StepState.SUCCEEDED)
        wfp.mark_step_pending(plan)
        assert plan.get_state() == StepState.PENDING

        # Nothing prevents the scheduler from dispatching `plan` again even though it has
        # a still-RUNNING child: creator-safety only flows from creator to product, never
        # the other way around (see `scheduler.SELECT_SAFE_UPDATE`).
        plan.set_state(StepState.RUNNING)
        plan.reset_for_rerun()

        # `sub` is now detached, but keeps running: it is not killed.
        assert sub.is_detached()
        assert sub.get_state() == StepState.RUNNING

        # The next RPC call made by `sub`'s still-alive child process is a harmless
        # no-op instead of a crash.
        assert amend_step(wfp, sub, inp_paths=["some_new_input"]) == (True, set(), set(), {})


async def test_mark_pending_noop_when_running(wfs: Workflow):
    """`mark_pending()` must leave a RUNNING step's state untouched."""
    async with wfs.db:
        wfs.define_step(wfs.root, "echo")
        echo = wfs.find(Step, "echo")
        echo.set_state(StepState.RUNNING)
        wfs.mark_step_pending(echo)
        assert echo.get_state() == StepState.RUNNING


async def test_mark_pending_noop_when_checking(wfs: Workflow):
    """`mark_pending()` must leave a CHECKING step's state untouched."""
    async with wfs.db:
        wfs.define_step(wfs.root, "echo")
        echo = wfs.find(Step, "echo")
        echo.set_state(StepState.CHECKING)
        wfs.mark_step_pending(echo)
        assert echo.get_state() == StepState.CHECKING


async def test_detach_marks_is_detached_regardless_of_state(wfp: Workflow):
    """`Step.detach()` marks a step as detached regardless of its current state.

    This complements `test_rerun_creator_detaches_running_child`, which only exercises a
    RUNNING child; here a SUCCEEDED child is detached too, via both a direct `detach()`
    call and `reset_for_rerun()`'s "detach steps created by this step" pass.
    """
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "sub1")
        wfp.define_step(plan, "sub2")
        sub1 = wfp.find(Step, "sub1")
        sub2 = wfp.find(Step, "sub2")

        # Direct detach() calls, regardless of state.
        sub1.set_state(StepState.RUNNING)
        sub1.detach()
        assert sub1.is_detached()

        sub2.set_state(StepState.SUCCEEDED)
        sub2.detach()
        assert sub2.is_detached()

        # Via reset_for_rerun()'s "detach steps created by this step" loop.
        sub1.reattach(plan)
        sub2.reattach(plan)
        sub1.set_state(StepState.RUNNING)
        sub2.set_state(StepState.SUCCEEDED)

        plan.set_state(StepState.SUCCEEDED)
        wfp.mark_step_pending(plan)
        plan.set_state(StepState.RUNNING)
        plan.reset_for_rerun()

        assert sub1.is_detached()
        assert sub2.is_detached()


async def test_declare_static_files_detached_creator_is_noop(wfp: Workflow):
    """A detached creator's `declare_static_files()` call must be a silent no-op."""
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "sub")
        sub = wfp.find(Step, "sub")
        sub.detach()
        assert wfp.declare_static_files(sub, ["ghost.txt"]) == {}
        assert wfp.find_and_detached(File, "ghost.txt") == (None, None)


async def test_register_static_tree_detached_creator_is_noop(wfp: Workflow):
    """A detached creator's `register_static_tree()` call must be a silent no-op."""
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "sub")
        sub = wfp.find(Step, "sub")
        sub.detach()
        assert wfp.register_static_tree(sub, "ghost_dir") == {}
        assert wfp.find_and_detached(StaticTree, "ghost_dir/") == (None, None)


async def test_define_step_detached_creator_is_noop(wfp: Workflow):
    """A detached creator's `define_step()` call must be a silent no-op."""
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "sub")
        sub = wfp.find(Step, "sub")
        sub.detach()
        assert wfp.define_step(sub, "echo ghost") == {}
        assert wfp.find_and_detached(Step, "echo ghost") == (None, None)


async def test_record_subprocess_detached_step_is_noop(wfp: Workflow):
    """A detached step's `record_subprocess()` call must be a silent no-op."""
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "sub")
        sub = wfp.find(Step, "sub")
        sub.detach()
        sub.add_subprocess("ghost cmd", ".", None, 0, False, "", "", "")
        count = wfp.db.execute(
            "SELECT COUNT(*) FROM step_subprocess WHERE node = ?", (sub.i,)
        ).fetchone()[0]
        assert count == 0


async def test_get_info_detached_step_returns_empty(wfp: Workflow):
    """A detached step's `get_info()` call must return an empty `StepInfo`."""
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "sub")
        sub = wfp.find(Step, "sub")
        sub.detach()
        assert sub.get_info() == StepInfo("", [], [], [], [], Path("."))


async def test_define_step_input_static(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        to_check = wfp.define_step(plan, "cat given", inp_paths=["given"])
        assert to_check == {}
        cat = wfp.find(Step, "cat given")
        assert cat.get_state() == StepState.PENDING
        given = wfp.find(File, "given")
        assert given.get_state() == FileState.AWAITED
        declare_static(wfp, plan, ["given"])
        assert given.get_state() == FileState.STATIC


async def test_define_step_static_input(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        declare_static(wfp, plan, ["given"])
        wfp.define_step(plan, "cat given", inp_paths=["given"])
        cat = wfp.find(Step, "cat given")
        assert cat.get_state() == StepState.PENDING
        given = wfp.find(File, "given")
        assert given.get_state() == FileState.STATIC


async def test_define_step_volatile_input(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "touch given", vol_paths=["given"])
        touch = wfp.find(Step, "touch given")
        file, detached = wfp.find_and_detached(File, "given")
        assert not detached
        assert file.get_state() == FileState.VOLATILE
    with pytest.raises(GraphError):
        async with wfp.db:
            # Volatile files are not allowed as inputs
            wfp.define_step(plan, "cat given", inp_paths=["given"])
    async with wfp.db:
        touch.mark_completed(StepHash(b"mock", None, b"zzz", None), False)
        assert touch.get_state() == StepState.SUCCEEDED
        assert file.get_state() == FileState.VOLATILE
    with pytest.raises(GraphError):
        async with wfp.db:
            # Volatile files are not allowed as inputs
            wfp.define_step(plan, "cat given", inp_paths=["given"])


async def test_define_step_input_volatile(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "cat given", inp_paths=["given"])
        cat = wfp.find(Step, "cat given")
        assert cat.get_state() == StepState.PENDING
        file, detached = wfp.find_and_detached(File, "given")
        assert detached
        assert file.get_state() == FileState.AWAITED
    with pytest.raises(GraphError):
        async with wfp.db:
            wfp.define_step(plan, "touch given", vol_paths=["given"])


async def test_file_state_static_overlap(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        declare_static(wfp, plan, ["given"])
    with pytest.raises(GraphError):
        async with wfp.db:
            wfp.define_step(plan, "touch given", out_paths=["given"])
    with pytest.raises(GraphError):
        async with wfp.db:
            wfp.define_step(plan, "touch given", vol_paths=["given"])
    async with wfp.db:
        wfp.define_step(plan, "echo", inp_paths=["some"], out_paths=["other"])
        step = wfp.find(Step, "echo")
        step.set_state(StepState.RUNNING)
        carry_on, to_check = _amend(
            wfp, step, inp_paths=["inp"], out_paths=["out"], vol_paths=["vol"]
        )
        assert not carry_on
        assert to_check == {}
        # Amending an existing input is tolerated.
        amend_step(wfp, step, inp_paths=["some"])
    with pytest.raises(GraphError):
        async with wfp.db:
            amend_step(wfp, step, out_paths=["given"])
    with pytest.raises(GraphError):
        async with wfp.db:
            amend_step(wfp, step, vol_paths=["given"])


async def test_file_state_output_overlap(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "touch given", out_paths=["given"])
    with pytest.raises(GraphError):
        async with wfp.db:
            declare_static(wfp, plan, ["given"])
    with pytest.raises(GraphError):
        async with wfp.db:
            wfp.define_step(plan, "touch given", vol_paths=["given"])
    async with wfp.db:
        wfp.define_step(plan, "echo", inp_paths=["some"], out_paths=["other"])
        step = wfp.find(Step, "echo")
        step.set_state(StepState.RUNNING)
        carry_on, to_check = _amend(
            wfp, step, inp_paths=["inp", "given"], out_paths=["out"], vol_paths=["vol"]
        )
        assert not carry_on
        assert to_check == {}
    with pytest.raises(GraphError):
        async with wfp.db:
            amend_step(wfp, step, out_paths=["given"])
    with pytest.raises(GraphError):
        async with wfp.db:
            amend_step(wfp, step, vol_paths=["given"])


async def test_file_state_volatile_overlap(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "touch given", vol_paths=["given"])
    with pytest.raises(GraphError):
        async with wfp.db:
            declare_static(wfp, plan, ["given"])
    with pytest.raises(GraphError):
        async with wfp.db:
            wfp.define_step(plan, "touch given", out_paths=["given"])
    async with wfp.db:
        wfp.define_step(plan, "echo", inp_paths=["some"], out_paths=["other"])
        step = wfp.find(Step, "echo")
        step.set_state(StepState.RUNNING)
        carry_on, to_check = _amend(
            wfp, step, inp_paths=["inp"], out_paths=["out"], vol_paths=["vol"]
        )
        assert not carry_on
        assert to_check == {}
    with pytest.raises(GraphError):
        async with wfp.db:
            amend_step(wfp, step, inp_paths=["given"])
    with pytest.raises(GraphError):
        async with wfp.db:
            amend_step(wfp, step, out_paths=["given"])
    with pytest.raises(GraphError):
        async with wfp.db:
            amend_step(wfp, step, vol_paths=["given"])


PENDING_STEP_SKIP_GRAPH = """\
root:
             product   file:plan.py
             product   step:./plan.py

file:plan.py
               state = STATIC
              digest = 4e929dac d83345e7 26c42517 5f6089aa 9b9513af 07615728 a82225e3 1383ff4f
             creator   root:
                sink   step:./plan.py

step:./plan.py
               state = PENDING
                need = PLAN
             creator   root:
              source   file:plan.py
             product   file:inp
             product   step:cat < inp > out

file:inp
               state = STATIC
              digest = 29a9e775 80ac85ad 896542d4 5ae52e21 8428bbe9 b0c752bc 2785ed22 a6eca01a
             creator   step:./plan.py
                sink   step:cat < inp > out

step:cat < inp > out
               state = SUCCEEDED
                need = DEFAULT
          inp_digest = 61616161 61616161 61616161 61616161 61616161 61616161 61616161 61616161
          out_digest = 62626262 62626262 62626262 62626262 62626262 62626262 62626262 62626262
             creator   step:./plan.py
              source   file:inp
             product   file:out
                sink   file:out

file:out
               state = BUILT
              digest = 762069bc 07a6e1b5 df123a5a e7bd91c1 0daa0469 4fbaa17f ba0cd6a8 dcce8f22
             creator   step:cat < inp > out
              source   step:cat < inp > out
"""


async def test_define_pending_step_skip(wfp: Workflow):
    async with wfp.db:
        # Define workflow
        plan = wfp.find(Step, "./plan.py")
        declare_static(wfp, plan, ["inp"])
        wfp.define_step(plan, "cat < inp > out", inp_paths=["inp"], out_paths=["out"])
        step = wfp.find(Step, "cat < inp > out")

        # Simulate run (first get the plan step and ignore it)
        wfp.update_file_hashes({"out": fake_hash("out")}, cause=HashUpdateCause.SUCCEEDED)
        step.mark_completed(StepHash(b"a" * 32, None, b"b" * 32, None), False)

        # Check run
        assert step.get_state() == StepState.SUCCEEDED
        step_hash = step.get_hash()
        assert step_hash.inp_digest == b"a" * 32
        assert step_hash.out_digest == b"b" * 32
        assert wfp.format_str() == PENDING_STEP_SKIP_GRAPH

        # Simulate input change
        wfp.update_file_hashes({"inp": fake_hash("inp")}, cause=HashUpdateCause.EXTERNAL)
        assert step.get_state() == StepState.PENDING
        out = wfp.find(File, "out")
        assert out.get_state() == FileState.OUTDATED

        # Simulate rerun
        assert step.get_state() == StepState.PENDING
        step.mark_completed(StepHash(b"a" * 32, None, b"b" * 32, None), False)
        assert wfp.format_str() == PENDING_STEP_SKIP_GRAPH
        assert step.get_state() == StepState.SUCCEEDED
        step.delete_hash()
        assert step.get_hash() is None


async def test_define_pending_step_skip_extra(wfp: Workflow):
    async with wfp.db:
        # Prepare jobs for normal run
        plan = wfp.find(Step, "./plan.py")
        declare_static(wfp, plan, ["ainp", "ainp2"])
        wfp.define_step(plan, "foo > log", env_deps=["VAR"], out_paths=["log"])
        foo = wfp.find(Step, "foo > log")
        assert foo.get_state() == StepState.PENDING
        wfp.define_step(foo, "bar > spam", inp_paths=["log"], env_deps=["X"], vol_paths=["spam"])
        bar = wfp.find(Step, "bar > spam")
        assert bar.get_state() == StepState.PENDING
        plan.mark_completed(StepHash(b"plan_ok", None, b"zzz", None), False)

        # Simulate run
        # foo
        carry_on, to_check = _amend(
            wfp, foo, inp_paths=["ainp"], out_paths=["aout"], vol_paths=["avol"]
        )
        assert carry_on
        assert to_check == {}
        wfp.update_file_hashes(
            {"log": fake_hash("log"), "aout": fake_hash("aout")}, cause=HashUpdateCause.SUCCEEDED
        )
        foo.mark_completed(StepHash(b"foo_ok", None, b"zzz", None), False)
        assert foo.get_state() == StepState.SUCCEEDED
        assert bar.get_state() == StepState.PENDING
        # bar
        wfp.mark_step_pending(bar)  # Should not hurt
        assert bar.get_state() == StepState.PENDING
        amend_step(wfp, bar, inp_paths=["ainp2"], out_paths=["aout2"], vol_paths=["avol2"])
        assert wfp.find(File, "ainp2") in set(bar.sources())
        wfp.update_file_hashes({"aout2": fake_hash("aout2")}, cause=HashUpdateCause.SUCCEEDED)
        bar.mark_completed(StepHash(b"bar_ok", None, b"zzz", None), False)
        assert bar.get_state() == StepState.SUCCEEDED
        txt = wfp.format_str()

        # Make foo pending and check state
        wfp.mark_step_pending(foo)
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
        wfp.mark_step_pending(foo)
        assert foo.get_state() == StepState.PENDING
        assert bar.get_state() == StepState.PENDING
        # This simulation assumes that no files have changed and we can just skip foo.
        foo.mark_completed(StepHash(b"foo_ok", None, b"zzz", None), False)
        assert foo.get_state() == StepState.SUCCEEDED
        assert bar.get_state() == StepState.PENDING
        # This simulation assumes that no files have changed and we can just skip bar
        bar.mark_completed(StepHash(b"bar_ok", None, b"zzz", None), False)
        assert bar.get_state() == StepState.SUCCEEDED
        assert wfp.format_str() == txt


async def test_skip_step_dynamic_detached_input(wfp: Workflow):
    async with wfp.db:
        # Prepare jobs for normal run
        plan = wfp.find(Step, "./plan.py")
        (ainp,) = declare_static(wfp, plan, ["ainp"])
        wfp.define_step(plan, "foo > log", out_paths=["log"])
        foo = wfp.find(Step, "foo > log")
        assert foo.get_state() == StepState.PENDING
        assert [r.path for r in foo.out_paths()] == ["log"]

        # Simulate run
        amend_step(wfp, foo, inp_paths=["ainp"], out_paths=["aout"], vol_paths=["avol"])
        wfp.update_file_hashes(
            {"log": fake_hash("log"), "aout": fake_hash("aout")}, cause=HashUpdateCause.SUCCEEDED
        )
        foo.mark_completed(StepHash(b"foo_ok", None, b"zzz", None), False)
        assert foo.get_state() == StepState.SUCCEEDED

        # Detach ainp and check state
        assert {r.path for r in foo.out_paths()} == {"aout", "log"}
        ainp.detach()
        assert {r.path for r in foo.out_paths()} == {"aout", "log"}
        assert foo.get_hash() is not None
        assert foo.get_state() == StepState.SUCCEEDED
        assert wfp.find(File, "log").get_state() == FileState.BUILT

        # When ainp reappears, foo should be rerun because ainp may have changed.
        declare_static(wfp, plan, ["ainp"])
        assert foo.get_hash() is not None
        assert foo.get_state() == StepState.PENDING
        log = wfp.find(File, "log")
        assert log.get_state() == FileState.OUTDATED


async def test_skip_nglob(wfp: Workflow):
    async with wfp.db:
        # Prepare jobs for normal run
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "foo")
        foo = wfp.find(Step, "foo")
        assert foo.get_state() == StepState.PENDING
        plan.mark_completed(StepHash(b"plan_ok", None, b"ee", None), False)
        assert plan.get_state() == StepState.SUCCEEDED

        # Simulate run
        ng = NamedGlob("${*prefix}_data.txt", subs={"prefix": "n???"})
        wfp.register_nglob(foo, ng)
        foo.mark_completed(StepHash(b"foo_ok", None, b"zzz", None), False)
        assert foo.get_hash() is not None
        assert foo.get_state() == StepState.SUCCEEDED

        # Make foo pending and check state
        wfp.mark_step_pending(foo)
        assert foo.get_hash() is not None
        assert foo.get_state() == StepState.PENDING

        # Skip
        assert foo.get_state() == StepState.PENDING
        foo.mark_completed(StepHash(b"foo_ok", None, b"zzz", None), False)
        assert foo.get_state() == StepState.SUCCEEDED
        nglobs = list(foo.nglobs())
        assert len(nglobs) == 1
        assert nglobs[0].pattern == "${*prefix}_data.txt"
        assert nglobs[0].subs == {"prefix": "n???"}
        assert nglobs[0].used_names == ("prefix",)


async def test_hash_completed_success(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "cp foo bar", inp_paths=["foo"], out_paths=["bar"])
        step_hash = StepHash(b"p" * 32, None, b"p" * 32, None)
        plan.mark_completed(step_hash, False)
        assert step_hash == plan.get_hash()


async def test_amend_step(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "blub > log", vol_paths=["log"])
        step = wfp.find(Step, "blub > log")
        assert amend_step(wfp, step)
        carry_on, to_check = _amend(
            wfp, step, inp_paths=["inp1", "inp2"], out_paths=["out3"], vol_paths=["vol4"]
        )
        assert not carry_on
        assert to_check == {}
        assert {
            (r.path, r.detached) for r in step.inp_paths(include_detached=True, dynamic=True)
        } == {
            ("inp1", True),
            ("inp2", True),
        }
        assert {r.path for r in step.out_paths(dynamic=True)} == {"out3"}
        assert {r.path for r in step.vol_paths(dynamic=True)} == {"vol4"}
        step.mark_completed(None, True)
        step.set_state(StepState.PENDING)
        declare_static(wfp, plan, ["inp1"])
        step.set_state(StepState.PENDING)
        declare_static(wfp, plan, ["inp2"])
        assert [node.key() for node in step.products()] == ["file:log", "file:out3", "file:vol4"]


def _build_producer_sink(wfs: Workflow) -> tuple[Step, Step]:
    """Define a producer step with a BUILT output "data.txt" and a plain sink step.

    Used by the freshness-check tests below to fabricate a BUILT input with a real
    `Step` creator, without needing the full director/scheduler stack.
    """
    wfs.define_step(wfs.root, "plan")
    plan = wfs.find(Step, "plan")

    wfs.define_step(plan, "producer", out_paths=["data.txt"])
    producer = wfs.find(Step, "producer")
    out_hashes = {"data.txt": fake_hash("data.txt")}
    wfs.update_file_hashes(out_hashes, cause=HashUpdateCause.SUCCEEDED)
    step_hash = StepHash.from_inp(producer.key(), True, {}, {})
    step_hash = step_hash.evolve_out(out_hashes)
    producer.mark_completed(step_hash, False)

    wfs.define_step(plan, "sink")
    sink = wfs.find(Step, "sink")
    return producer, sink


async def test_amend_step_never_concurrent_skips_freshness_check(wfs: Workflow):
    """When `ran_concurrently` always reports no overlap, a BUILT input is always
    accepted, regardless of any race."""
    async with wfs.db:
        _, sink = _build_producer_sink(wfs)
        is_detached, unavailable, unfresh, _ = amend_step(
            wfs, sink, inp_paths=["data.txt"], ran_concurrently=lambda p, c: False
        )
        assert not is_detached
        assert not unavailable
        assert not unfresh


async def test_amend_step_freshness_fresh(wfs: Workflow):
    """`ran_concurrently` reports no overlap: input accepted as fresh."""
    async with wfs.db:
        _, sink = _build_producer_sink(wfs)
        is_detached, unavailable, unfresh, _ = amend_step(
            wfs, sink, inp_paths=["data.txt"], ran_concurrently=lambda p, c: False
        )
        assert not is_detached
        assert not unavailable
        assert not unfresh


async def test_amend_step_freshness_unfresh(wfs: Workflow):
    """`ran_concurrently` reports an overlap: input rejected as unfresh."""
    async with wfs.db:
        _, sink = _build_producer_sink(wfs)
        is_detached, unavailable, unfresh, _ = amend_step(
            wfs, sink, inp_paths=["data.txt"], ran_concurrently=lambda p, c: True
        )
        assert not is_detached
        assert not unavailable
        assert unfresh == {"data.txt"}


PENDING_STEP_SKIP_DYNAMIC_GRAPH = """\
root:
             product   file:plan.py
             product   step:./plan.py

file:plan.py
               state = STATIC
              digest = 4e929dac d83345e7 26c42517 5f6089aa 9b9513af 07615728 a82225e3 1383ff4f
             creator   root:
                sink   step:./plan.py

step:./plan.py
               state = PENDING
                need = PLAN
             creator   root:
              source   file:plan.py
             product   file:ainp
             product   file:inp
             product   step:cat < inp > out 2> vol

file:ainp
               state = STATIC
              digest = c0a3760b 3f6ad19a 940952bc 5e60a7e3 e6554d97 f19114b7 765e21e0 a14cf4d6
             creator   step:./plan.py
                sink   step:cat < inp > out 2> vol

file:inp
               state = STATIC
              digest = 29a9e775 80ac85ad 896542d4 5ae52e21 8428bbe9 b0c752bc 2785ed22 a6eca01a
             creator   step:./plan.py
                sink   step:cat < inp > out 2> vol

step:cat < inp > out 2> vol
               state = SUCCEEDED
                need = DEFAULT
          inp_digest = 63636363 63636363 63636363 63636363 63636363 63636363 63636363 63636363
          out_digest = 64646464 64646464 64646464 64646464 64646464 64646464 64646464 64646464
             creator   step:./plan.py
              source   file:ainp [dynamic]
              source   file:inp
             product   file:aout
             product   file:avol
             product   file:out
             product   file:vol
                sink   file:aout [dynamic]
                sink   file:avol [dynamic]
                sink   file:out
                sink   file:vol

file:out
               state = BUILT
              digest = 762069bc 07a6e1b5 df123a5a e7bd91c1 0daa0469 4fbaa17f ba0cd6a8 dcce8f22
             creator   step:cat < inp > out 2> vol
              source   step:cat < inp > out 2> vol

file:vol
               state = VOLATILE
             creator   step:cat < inp > out 2> vol
              source   step:cat < inp > out 2> vol

file:aout
               state = BUILT
              digest = bff8fd60 206e04a5 f6052fe5 5896f8da b0fb3f74 fd92802e d68adedb 7b082496
             creator   step:cat < inp > out 2> vol
              source   step:cat < inp > out 2> vol

file:avol
               state = VOLATILE
             creator   step:cat < inp > out 2> vol
              source   step:cat < inp > out 2> vol
"""


async def test_define_pending_step_skip_dynamic(wfp: Workflow):
    async with wfp.db:
        # Define workflow
        plan = wfp.find(Step, "./plan.py")
        declare_static(wfp, plan, ["inp", "ainp"])
        wfp.define_step(
            plan, "cat < inp > out 2> vol", inp_paths=["inp"], out_paths=["out"], vol_paths=["vol"]
        )
        step = wfp.find(Step, "cat < inp > out 2> vol")

        # Simulate running the step
        amend_step(wfp, step, inp_paths=["ainp"], out_paths=["aout"], vol_paths=["avol"])
        wfp.update_file_hashes(
            {"out": fake_hash("out"), "aout": fake_hash("aout")}, cause=HashUpdateCause.SUCCEEDED
        )
        step.mark_completed(StepHash(b"c" * 32, None, b"d" * 32, None), False)
        assert wfp.format_str() == PENDING_STEP_SKIP_DYNAMIC_GRAPH
        assert step.get_state() == StepState.SUCCEEDED

        # Check run
        assert step.get_state() == StepState.SUCCEEDED
        step_hash = step.get_hash()
        assert step_hash.inp_digest == b"c" * 32
        assert step_hash.out_digest == b"d" * 32

        # Simulate dynamic input change
        wfp.update_file_hashes({"ainp": fake_hash("ainp")}, cause=HashUpdateCause.EXTERNAL)
        assert step.get_state() == StepState.PENDING
        out = wfp.find(File, "out")
        assert out.get_state() == FileState.OUTDATED

        # Simulate and check rerun
        assert step.get_state() == StepState.PENDING
        step.mark_completed(StepHash(b"c" * 32, None, b"d" * 32, None), False)
        assert {node.key() for node in step.sources(include_detached=True)} == {
            "file:ainp",
            "file:inp",
        }
        assert isinstance(wfp.find(File, "aout"), File)
        assert isinstance(wfp.find(File, "avol"), File)
        assert {node.key() for node in step.sources(include_detached=True)} == {
            "file:ainp",
            "file:inp",
        }
        assert wfp.find(File, "aout").creator() == step
        assert wfp.find(File, "avol").creator() == step
        assert wfp.format_str() == PENDING_STEP_SKIP_DYNAMIC_GRAPH
        assert step.get_state() == StepState.SUCCEEDED

        # Check deletion of hash
        step.delete_hash()
        assert step.get_hash() is None


REGISTER_NGLOB_GRAPH = """\
root:
             product   file:plan.py
             product   step:./plan.py

file:plan.py
               state = STATIC
              digest = 4e929dac d83345e7 26c42517 5f6089aa 9b9513af 07615728 a82225e3 1383ff4f
             creator   root:
                sink   step:./plan.py

step:./plan.py
               state = PENDING
                need = PLAN
             creator   root:
              source   file:plan.py
             product   step:touch log

step:touch log
               state = PENDING
                need = DEFAULT
               nglob = *.txt
             creator   step:./plan.py
             product   file:log
                sink   file:log

file:log
               state = VOLATILE
             creator   step:touch log
              source   step:touch log
"""


async def test_register_glob(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "touch log", vol_paths=["log"])
        step = wfp.find(Step, "touch log")
        ng = NamedGlob("*.txt")
        wfp.register_nglob(step, ng)
        assert list(step.nglobs()) == [ng]
        assert list(wfp.nglob_registrations()) == [(1, ng, step)]
        assert wfp.format_str() == REGISTER_NGLOB_GRAPH

        # Detaching does not clear the row, but Workflow.nglobs() must skip it: a detached
        # leftover pattern must not be visible to the eager checks Phase 2 adds.
        step.detach()
        assert list(step.nglobs()) == [ng]
        assert list(wfp.nglob_registrations()) == []
        wfp.delete_detached()
        assert list(step.nglobs()) == []
        assert list(wfp.nglob_registrations()) == []


async def test_nglobs_skips_detached_step(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "touch log", vol_paths=["log"])
        step = wfp.find(Step, "touch log")
        ng = NamedGlob("*.txt")
        wfp.register_nglob(step, ng)
        assert list(wfp.nglobs()) == [ng]
        assert wfp.change_is_relevant("foo.txt")

        step.detach()
        assert list(wfp.nglobs()) == []
        assert not wfp.change_is_relevant("foo.txt")


async def test_change_is_relevant(wfp: Workflow):
    async with wfp.db:
        assert wfp.change_is_relevant("plan.py")
        assert not wfp.change_is_relevant("unknown.txt")
        plan = wfp.find(Step, "./plan.py")
        wfp.register_nglob(plan, NamedGlob("*.txt"))
        assert wfp.change_is_relevant("unknown.txt")


async def test_change_is_relevant_glob_match_without_node(wfp: Workflow):
    """A path with no node of its own is judged solely by the registered glob patterns.

    `change_is_relevant` takes only a path, so it cannot tell whether the caller observed an
    update or a deletion: it must answer the same way either way, which is what lets
    `Watcher.record_change` use it for both `Change.UPDATED` and `Change.DELETED`.
    """
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.register_nglob(plan, NamedGlob("*.txt"))
        assert wfp.find(File, "unknown.txt") is None
        assert wfp.change_is_relevant("unknown.txt")
        assert wfp.change_is_relevant("unknown.txt")
        assert not wfp.change_is_relevant("unknown.dat")


async def test_change_is_relevant_during_build_glob_match_without_node(wfp: Workflow):
    """Stricter than `change_is_relevant` for a real node, but the same for a nodeless glob
    match: no step can be building it, since a pattern may not match a build product.
    """
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.register_nglob(plan, NamedGlob("*.txt"))
        assert wfp.find(File, "unknown.txt") is None
        assert wfp.change_is_relevant_during_build("unknown.txt")
        assert not wfp.change_is_relevant_during_build("unknown.dat")


async def test_relevant_paths_under_includes_glob_matches(wfp: Workflow):
    """`relevant_paths_under` (the `Change.DELETED_PARENT` handler) must also yield the
    recorded matches of registered glob patterns under `parent`, since a match has no
    file node for the node-based half of the query to find.
    """
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        declare_static(wfp, plan, ["data/kept.txt"])
        ng = NamedGlob("data/*.txt")
        # kept.txt is both a real node and a glob match: it must be reported once, not
        # twice. nomatch.txt has no node at all.
        ng.extend(["data/kept.txt", "data/nomatch.txt"])
        wfp.register_nglob(plan, ng)

        assert set(wfp.relevant_paths_under("data/")) == {"data/kept.txt", "data/nomatch.txt"}


async def test_register_glob_rejects_built_match(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "producer", out_paths=["out.txt"])
        producer = wfp.find(Step, "producer")
        wfp.update_file_hashes({"out.txt": fake_hash("out.txt")}, cause=HashUpdateCause.SUCCEEDED)
        producer.mark_completed(StepHash(b"p" * 32, None, b"q" * 32, None), False)
        assert wfp.find(File, "out.txt").get_state() == FileState.BUILT

        ng = NamedGlob("*.txt")
        ng.extend(["out.txt"])
        with pytest.raises(GraphError, match=r"which step \(producer\) builds"):
            wfp.register_nglob(plan, ng)


async def test_register_glob_rejects_awaited_match(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "producer", out_paths=["out.txt"])
        assert wfp.find(File, "out.txt").get_state() == FileState.AWAITED

        ng = NamedGlob("*.txt")
        ng.extend(["out.txt"])
        with pytest.raises(GraphError, match=r"which step \(producer\) builds"):
            wfp.register_nglob(plan, ng)


async def test_register_glob_rejects_volatile_match(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "producer", vol_paths=["out.txt"])
        assert wfp.find(File, "out.txt").get_state() == FileState.VOLATILE

        ng = NamedGlob("*.txt")
        ng.extend(["out.txt"])
        with pytest.raises(GraphError, match=r"which step \(producer\) builds"):
            wfp.register_nglob(plan, ng)


async def test_register_glob_rejects_outdated_match(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        declare_static(wfp, plan, ["inp.txt"])
        wfp.define_step(plan, "producer", inp_paths=["inp.txt"], out_paths=["out.txt"])
        producer = wfp.find(Step, "producer")
        wfp.update_file_hashes({"out.txt": fake_hash("out.txt")}, cause=HashUpdateCause.SUCCEEDED)
        producer.mark_completed(StepHash(b"p" * 32, None, b"q" * 32, None), False)
        assert wfp.find(File, "out.txt").get_state() == FileState.BUILT

        # Changing the input makes the output OUTDATED without rerunning the step.
        wfp.update_file_hashes({"inp.txt": fake_hash("changed")}, cause=HashUpdateCause.EXTERNAL)
        assert wfp.find(File, "out.txt").get_state() == FileState.OUTDATED

        ng = NamedGlob("*.txt")
        ng.extend(["out.txt"])
        with pytest.raises(GraphError, match=r"which step \(producer\) builds"):
            wfp.register_nglob(plan, ng)


async def test_register_glob_ignores_detached_awaited_match(wfp: Workflow):
    """An unresolved input created by `_resolve_supply_file` (creator=None, forced
    detached by `Trellis.create`) is not a claim that some step builds it, unlike an
    output declared by `define_step`/`amend_step`.
    """
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "consumer", inp_paths=["missing.txt"])
        missing = wfp.find(File, "missing.txt")
        assert missing.get_state() == FileState.AWAITED
        assert missing.is_detached()

        ng = NamedGlob("*.txt")
        ng.extend(["missing.txt"])
        wfp.register_nglob(plan, ng)  # must not raise
        assert list(wfp.nglobs()) == [ng]


async def test_register_glob_accepts_static_and_missing_and_unconfirmed(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        declare_static(wfp, plan, ["static.txt"])
        assert wfp.find(File, "static.txt").get_state() == FileState.STATIC

        wfp.declare_static_files(plan, ["unconfirmed.txt"])
        assert wfp.find(File, "unconfirmed.txt").get_state() == FileState.UNCONFIRMED

        wfp.declare_static_files(plan, ["missing.txt"])
        wfp.update_file_hashes({"missing.txt": FileHash.unknown()}, cause=HashUpdateCause.CONFIRMED)
        assert wfp.find(File, "missing.txt").get_state() == FileState.MISSING

        ng = NamedGlob("*.txt")
        ng.extend(["static.txt", "unconfirmed.txt", "missing.txt"])
        wfp.register_nglob(plan, ng)  # must not raise
        assert list(wfp.nglobs()) == [ng]


async def test_register_glob_rejects_stepup_dir(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        ng = NamedGlob("*")
        ng.extend([f"{STEPUP_DIR}/"])
        with pytest.raises(GraphError, match=r"matches a path under \.stepup"):
            wfp.register_nglob(plan, ng)


#
# Workflow.find_glob_violations
#


async def test_find_glob_violations_static_file_no_violation(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        declare_static(wfp, plan, ["static.txt"])
        ng = NamedGlob("*.txt")
        ng.extend(["static.txt"])
        wfp.register_nglob(plan, ng)
        assert wfp.find_glob_violations() == []


async def test_find_glob_violations_unconfirmed_no_violation(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.declare_static_files(plan, ["unconfirmed.txt"])
        assert wfp.find(File, "unconfirmed.txt").get_state() == FileState.UNCONFIRMED
        ng = NamedGlob("*.txt")
        ng.extend(["unconfirmed.txt"])
        wfp.register_nglob(plan, ng)
        assert wfp.find_glob_violations() == []


async def test_find_glob_violations_missing_no_violation(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.declare_static_files(plan, ["missing.txt"])
        wfp.update_file_hashes({"missing.txt": FileHash.unknown()}, cause=HashUpdateCause.CONFIRMED)
        assert wfp.find(File, "missing.txt").get_state() == FileState.MISSING
        ng = NamedGlob("*.txt")
        ng.extend(["missing.txt"])
        wfp.register_nglob(plan, ng)
        assert wfp.find_glob_violations() == []


async def test_find_glob_violations_inside_static_tree_no_violation(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.register_static_tree(plan, "sub")
        ng = NamedGlob("sub/*.txt")
        ng.extend(["sub/inner.txt"])
        wfp.register_nglob(plan, ng)
        # The tree owns the file only once a step actually uses it as an input, so
        # there is no node of its own here.
        assert wfp.find(File, "sub/inner.txt") is None
        assert wfp.find_glob_violations() == []


async def test_find_glob_violations_dir_is_static_tree_root_no_violation(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.register_static_tree(plan, "sub")
        ng = NamedGlob("s*/")
        ng.extend(["sub/"])
        wfp.register_nglob(plan, ng)
        assert wfp.find_glob_violations() == []


async def test_find_glob_violations_dir_contains_static_tree_no_violation(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.register_static_tree(plan, "outer/inner")
        ng = NamedGlob("o*/")
        ng.extend(["outer/"])
        wfp.register_nglob(plan, ng)
        assert wfp.find_glob_violations() == []


async def test_find_glob_violations_dir_contains_static_file_no_violation(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        declare_static(wfp, plan, ["sub/inner.txt"])
        ng = NamedGlob("s*/")
        ng.extend(["sub/"])
        wfp.register_nglob(plan, ng)
        assert wfp.find_glob_violations() == []


async def test_find_glob_violations_stale_match_not_on_disk_no_violation(wfp: Workflow, tmpdir):
    with contextlib.chdir(tmpdir):
        async with wfp.db:
            plan = wfp.find(Step, "./plan.py")
            ng = NamedGlob("*.txt")
            ng.extend(["gone.txt"])
            wfp.register_nglob(plan, ng)
            assert wfp.find_glob_violations() == []


async def test_find_glob_violations_no_node_on_disk_violation(wfp: Workflow, tmpdir):
    with contextlib.chdir(tmpdir):
        with open("orphan.txt", "w") as fh:
            fh.write("x")
        async with wfp.db:
            plan = wfp.find(Step, "./plan.py")
            ng = NamedGlob("*.txt")
            ng.extend(["orphan.txt"])
            wfp.register_nglob(plan, ng)
            violations = wfp.find_glob_violations()
            assert violations == [GlobViolation(plan.label, ng.pattern, "orphan.txt", None)]
            assert not violations[0].is_error


async def test_find_glob_violations_awaited_violation(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "producer", out_paths=["out.txt"])
        assert wfp.find(File, "out.txt").get_state() == FileState.AWAITED

        ng = NamedGlob("*.txt")
        ng.extend(["out.txt"])
        # Workflow.register_nglob's eager check (workflow.py:1317-1344) would reject this
        # match; the AWAITED arm in find_glob_violations is defensive, covering a gap in
        # that check rather than something reachable through it. Register directly via
        # Step.add_nglob to bypass the eager check and exercise the defensive arm.
        plan.add_nglob(ng)
        violations = wfp.find_glob_violations()
        assert violations == [GlobViolation(plan.label, ng.pattern, "out.txt", FileState.AWAITED)]
        assert not violations[0].is_error


async def test_find_glob_violations_built_violation(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "producer", out_paths=["out.txt"])
        producer = wfp.find(Step, "producer")
        wfp.update_file_hashes({"out.txt": fake_hash("out.txt")}, cause=HashUpdateCause.SUCCEEDED)
        producer.mark_completed(StepHash(b"p" * 32, None, b"q" * 32, None), False)
        assert wfp.find(File, "out.txt").get_state() == FileState.BUILT

        ng = NamedGlob("*.txt")
        ng.extend(["out.txt"])
        plan.add_nglob(ng)  # bypass the eager check; see test_..._awaited_violation
        violations = wfp.find_glob_violations()
        assert violations == [GlobViolation(plan.label, ng.pattern, "out.txt", FileState.BUILT)]
        assert violations[0].is_error


async def test_find_glob_violations_outdated_violation(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        declare_static(wfp, plan, ["inp.txt"])
        wfp.define_step(plan, "producer", inp_paths=["inp.txt"], out_paths=["out.txt"])
        producer = wfp.find(Step, "producer")
        wfp.update_file_hashes({"out.txt": fake_hash("out.txt")}, cause=HashUpdateCause.SUCCEEDED)
        producer.mark_completed(StepHash(b"p" * 32, None, b"q" * 32, None), False)
        wfp.update_file_hashes({"inp.txt": fake_hash("changed")}, cause=HashUpdateCause.EXTERNAL)
        assert wfp.find(File, "out.txt").get_state() == FileState.OUTDATED

        ng = NamedGlob("*.txt")
        ng.extend(["out.txt", "inp.txt"])
        plan.add_nglob(ng)  # bypass the eager check; see test_..._awaited_violation
        violations = wfp.find_glob_violations()
        # inp.txt is still STATIC (an EXTERNAL update that still hashes keeps it STATIC),
        # so only out.txt is unjustified.
        assert violations == [GlobViolation(plan.label, ng.pattern, "out.txt", FileState.OUTDATED)]
        assert violations[0].is_error


async def test_find_glob_violations_volatile_violation(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "producer", vol_paths=["out.txt"])
        assert wfp.find(File, "out.txt").get_state() == FileState.VOLATILE

        ng = NamedGlob("*.txt")
        ng.extend(["out.txt"])
        plan.add_nglob(ng)  # bypass the eager check; see test_..._awaited_violation
        violations = wfp.find_glob_violations()
        assert violations == [GlobViolation(plan.label, ng.pattern, "out.txt", FileState.VOLATILE)]
        assert violations[0].is_error


async def test_find_glob_violations_detached_step_no_violation(wfp: Workflow, tmpdir):
    with contextlib.chdir(tmpdir):
        with open("orphan.txt", "w") as fh:
            fh.write("x")
        async with wfp.db:
            plan = wfp.find(Step, "./plan.py")
            wfp.define_step(plan, "touch log", vol_paths=["log"])
            step = wfp.find(Step, "touch log")
            ng = NamedGlob("*.txt")
            ng.extend(["orphan.txt"])
            wfp.register_nglob(step, ng)
            step.detach()
            assert list(wfp.nglob_registrations()) == []
            assert wfp.find_glob_violations() == []


async def test_find_glob_violations_detached_node_still_violation(wfp: Workflow, tmpdir):
    """A detached node never counts as justification: it falls through to the "no node"
    arms exactly like a path with no node at all (see the `nglobs`/`register_nglob`
    docstrings' "detached nodes never count" rationale).
    """
    with contextlib.chdir(tmpdir):
        with open("detached.txt", "w") as fh:
            fh.write("x")
        async with wfp.db:
            plan = wfp.find(Step, "./plan.py")
            wfp.define_step(plan, "consumer", inp_paths=["detached.txt"])
            detached = wfp.find(File, "detached.txt")
            assert detached.get_state() == FileState.AWAITED
            assert detached.is_detached()

            ng = NamedGlob("*.txt")
            ng.extend(["detached.txt"])
            wfp.register_nglob(plan, ng)  # must not raise: detached nodes never count
            violations = wfp.find_glob_violations()
            assert violations == [GlobViolation(plan.label, ng.pattern, "detached.txt", None)]
            assert not violations[0].is_error


async def test_find_glob_violations_two_patterns_two_violations_sorted(wfp: Workflow, tmpdir):
    with contextlib.chdir(tmpdir):
        with open("shared.txt", "w") as fh:
            fh.write("x")
        async with wfp.db:
            plan = wfp.find(Step, "./plan.py")
            wfp.define_step(plan, "step_a", vol_paths=["a.log"])
            wfp.define_step(plan, "step_b", vol_paths=["b.log"])
            step_a = wfp.find(Step, "step_a")
            step_b = wfp.find(Step, "step_b")
            ng_a = NamedGlob("*.txt")
            ng_a.extend(["shared.txt"])
            ng_b = NamedGlob("s*.txt")
            ng_b.extend(["shared.txt"])
            wfp.register_nglob(step_a, ng_a)
            wfp.register_nglob(step_b, ng_b)

            violations = wfp.find_glob_violations()
            assert violations == sorted(violations)
            assert violations == [
                GlobViolation(step_a.label, ng_a.pattern, "shared.txt", None),
                GlobViolation(step_b.label, ng_b.pattern, "shared.txt", None),
            ]


async def test_find_glob_violations_root_dir_justified_by_any_static_file(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        declare_static(wfp, plan, ["deep/nested/static.txt"])
        ng = NamedGlob("./")
        ng.extend(["./"])
        wfp.register_nglob(plan, ng)
        assert wfp.find_glob_violations() == []


async def test_define_step_output_matching_glob_raises(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.register_nglob(plan, NamedGlob("*.txt"))
        with pytest.raises(GraphError, match=r"which step \(touch out.txt\) builds"):
            wfp.define_step(plan, "touch out.txt", out_paths=["out.txt"])
        assert wfp.find(Step, "touch out.txt") is None


async def test_amend_step_output_matching_glob_raises(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.register_nglob(plan, NamedGlob("*.txt"))
        wfp.define_step(plan, "touch")
        step = wfp.find(Step, "touch")
        with pytest.raises(GraphError, match=r"which step \(touch\) builds"):
            amend_step(wfp, step, out_paths=["out.txt"])


async def test_define_step_output_matching_detached_glob_ok(wfp: Workflow):
    """A detached step keeps its `nglob` row (only `reset_for_rerun` deletes it), so the
    detached-pattern filter in `Workflow.nglobs()` must apply to check (b) too, or a
    leftover pattern from a step that has moved on would block a perfectly valid build.
    """
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "globber")
        globber = wfp.find(Step, "globber")
        wfp.register_nglob(globber, NamedGlob("*.txt"))
        globber.detach()

        wfp.define_step(plan, "touch out.txt", out_paths=["out.txt"])
        assert wfp.find(Step, "touch out.txt") is not None


async def test_eager_checks_agree_on_message_text(wfp: Workflow):
    """Check (a) (in `register_nglob`) and check (b) (in `_raise_if_glob_match`) must
    raise the exact same message for the same conflict, since the diagnostic must not
    depend on which of the two events happens first (open question 2)."""
    # (a): the output is declared first, so the file already has a node when the
    # pattern is registered.
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "touch out.txt", out_paths=["out.txt"])
        ng = NamedGlob("*.txt")
        ng.extend(["out.txt"])
        with pytest.raises(GraphError) as excinfo_a:
            wfp.register_nglob(plan, ng)

    # (b): an independent workflow with identical labels, but the pattern is
    # registered first and the output is declared afterwards. A second workflow is
    # used (rather than reusing wfp) so the labels can be identical to (a)'s, which is
    # what makes a literal string comparison meaningful. This mirrors the `wfp` fixture
    # in conftest.py.
    dir_queue = asyncio.Queue()
    with DBSession.open(":memory:") as db:
        wfp_b = Workflow(db, create_parent_dirs=False, dir_queue=dir_queue)
        await wfp_b.initialize()
        async with db:
            declare_static(wfp_b, wfp_b.root, ["plan.py"])
            wfp_b.define_step(wfp_b.root, "./plan.py", inp_paths=["plan.py"], need=Need.PLAN)
            plan_b = wfp_b.find(Step, "./plan.py")
            wfp_b.register_nglob(plan_b, NamedGlob("*.txt"))
            with pytest.raises(GraphError) as excinfo_b:
                wfp_b.define_step(plan_b, "touch out.txt", out_paths=["out.txt"])

    assert str(excinfo_a.value) == str(excinfo_b.value)


async def test_externally_updated1(wfp: Workflow):
    # Simulate creating and running two steps: one succeeds and one fails.
    # `aa1_bar.txt` and `bb7_bar.txt` are only ever glob matches, never declared or
    # built by any step: a glob pattern may not match a build product, so the step's
    # own output uses a name (`aa1_out.txt`) outside both registered patterns.
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        declare_static(wfp, plan, ["aa1_foo.txt", "bb7_foo.txt", "cc5_foo.txt"])
        paths = ["aa1_foo.txt", "aa1_bar.txt", "bb7_foo.txt", "cc5_foo.txt"]
        subs = {"prefix": "??[0-9]", "unused": "aa??"}
        ng_foo = NamedGlob("${*prefix}_foo.txt", subs)
        ng_foo.extend(paths)
        wfp.register_nglob(plan, ng_foo)
        ng_bar = NamedGlob("${*prefix}_bar.txt", subs)
        ng_bar.extend(paths)
        wfp.register_nglob(plan, ng_bar)
        wfp.define_step(
            plan, "work", inp_paths=["aa1_foo.txt"], out_paths=["aa1_out.txt"], vol_paths=["log"]
        )
        work = wfp.find(Step, "work")
        plan.mark_completed(StepHash(b"ok", None, b"inp_ok", None), False)
        aa1_out = wfp.find(File, "aa1_out.txt")
        assert aa1_out.creator() == work
        assert aa1_out.get_state() == FileState.AWAITED
        assert work.get_state() == StepState.PENDING
        wfp.update_file_hashes({"aa1_out.txt": fake_hash("ok")}, cause=HashUpdateCause.SUCCEEDED)
        work.mark_completed(None, False)
        assert work.get_state() == StepState.FAILED
        assert aa1_out.get_state() == FileState.OUTDATED
        assert list(wfp.steps(StepState.SUCCEEDED)) == [plan]
        assert list(wfp.steps(StepState.FAILED)) == [work]
        cc5_foo = wfp.find(File, "cc5_foo.txt")
        assert cc5_foo is not None
        assert cc5_foo.get_state() == FileState.STATIC
        print(cc5_foo.i)

    # Simulate external changes.
    async with wfp.db:
        # Changes:
        # - Delete `cc5_foo.txt` (static but not used)
        # - Update `aa1_out.txt` (output of work, must be repeated)
        # - Update `bb7_bar.txt` (not used, trigggers a change in the nglob results)
        wfp.update_file_hashes(
            {"cc5_foo.txt": FileHash.unknown(), "aa1_out.txt": fake_hash("change1")},
            cause=HashUpdateCause.EXTERNAL,
        )
        wfp.process_nglob_changes({"cc5_foo.txt"}, {"bb7_bar.txt"})

        # The top-level plan became pending (and pending again), so the step work becomes detached.
        assert work.get_state() == StepState.PENDING
        assert not work.is_detached()
        assert not aa1_out.is_detached()
        assert aa1_out.get_state() == FileState.AWAITED
        assert cc5_foo is not None
        assert cc5_foo.get_state() == FileState.MISSING
        assert wfp.find(File, "bb7_bar.txt") is None
        nglobs = list(plan.nglobs())
        assert len(nglobs) == 2
        assert sorted({*nglobs[0].files(), *nglobs[1].files()}) == [
            "aa1_bar.txt",
            "aa1_foo.txt",
            "bb7_bar.txt",
            "bb7_foo.txt",
        ]
        assert nglobs[0].results == {
            ("aa1",): {"aa1_foo.txt"},
            ("bb7",): {"bb7_foo.txt"},
        }
        assert nglobs[1].results == {
            ("aa1",): {"aa1_bar.txt"},
            ("bb7",): {"bb7_bar.txt"},
        }


async def test_externally_updated_static_detached(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        declare_static(wfp, plan, ["foo.txt"])
        foo = wfp.find(File, "foo.txt")
        foo.detach()
        foo.set_state(FileState.MISSING)
        wfp.update_file_hashes({"foo.txt": fake_hash("foo.txt")}, cause=HashUpdateCause.EXTERNAL)
        assert foo.is_detached()
        assert foo.get_state() == FileState.STATIC


async def test_externally_updated_static_missing(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        declare_static(wfp, plan, ["foo.txt"])
        foo = wfp.find(File, "foo.txt")
        foo.set_state(FileState.MISSING)
        wfp.update_file_hashes({"foo.txt": fake_hash("foo.txt")}, cause=HashUpdateCause.EXTERNAL)
        assert foo.creator().i == plan.i
        assert foo.get_state() == FileState.STATIC


async def test_externally_deleted_static_detached(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        declare_static(wfp, plan, ["foo.txt"])
        foo = wfp.find(File, "foo.txt")
        foo.detach()
        wfp.update_file_hashes({"foo.txt": FileHash.unknown()}, cause=HashUpdateCause.EXTERNAL)
        assert foo.is_detached()
        assert foo.get_state() == FileState.MISSING
        assert foo.get_hash() == FileHash.unknown()


async def test_externally_updated_built_detached(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "touch foo.txt", out_paths=["foo.txt"])
        step = wfp.find(Step, "touch foo.txt")
        step.detach()
        assert step.get_state() == StepState.PENDING
    with pytest.raises(AssertionError):
        async with wfp.db:
            wfp.update_file_hashes(
                {"foo.txt": fake_hash("foo.txt")}, cause=HashUpdateCause.EXTERNAL
            )
    async with wfp.db:
        assert step.get_state() == StepState.PENDING


async def test_externally_deleted_built_detached(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "touch foo.txt", out_paths=["foo.txt"])
        step = wfp.find(Step, "touch foo.txt")
        step.detach()
        assert step.get_state() == StepState.PENDING
    with pytest.raises(AssertionError):
        async with wfp.db:
            wfp.update_file_hashes({"foo.txt": FileHash.unknown()}, cause=HashUpdateCause.EXTERNAL)
    async with wfp.db:
        assert step.get_state() == StepState.PENDING


async def test_directory_usage(wfp: Workflow):
    async with wfp.db:
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
        wfp.delete_detached()
        events = []
        while not wfp.dir_queue.empty():
            events.append(wfp.dir_queue.get_nowait())
        assert events == []


async def test_to_be_deleted(wfp: Workflow):
    async with wfp.db:
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
            {"built": built_file_hash, "gone": gone_file_hash, "sub/foo": foo_file_hash},
            cause=HashUpdateCause.SUCCEEDED,
        )
        blub1.mark_completed(StepHash(b"aaa", None, b"zzz", None), False)
        plan.detach()
        assert wfp.to_be_deleted == {}
        assert wfp.find_and_detached(Step, "./plan.py") == (plan, True)
        wfp.delete_detached()
        assert wfp.to_be_deleted == {
            "built": built_file_hash,
            "gone": gone_file_hash,
            "volatile": None,
            "sub/foo": foo_file_hash,
        }
        assert wfp.find_and_detached(Step, "./plan.py") == (None, None)


async def test_externally_deleted(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        (tst,) = declare_static(wfp, wfp.root, ["tst"])
        wfp.define_step(plan, "bla1", out_paths=["prr"])
        step1 = wfp.find(Step, "bla1")
        wfp.define_step(plan, "bla2", inp_paths=["prr"])
        step2 = wfp.find(Step, "bla2")

        # Static
        wfp.update_file_hashes({"tst": FileHash.unknown()}, cause=HashUpdateCause.EXTERNAL)
        assert tst.get_state() == FileState.MISSING
    with pytest.raises(AssertionError):
        async with wfp.db:
            wfp.update_file_hashes({"tst": FileHash.unknown()}, cause=HashUpdateCause.EXTERNAL)

    async with wfp.db:
        # Built
        prr = wfp.find(File, "prr")
        assert prr.get_state() == FileState.AWAITED
    with pytest.raises(AssertionError):
        async with wfp.db:
            wfp.update_file_hashes({"prr": FileHash.unknown()}, cause=HashUpdateCause.EXTERNAL)
    async with wfp.db:
        assert prr.get_state() == FileState.AWAITED
        wfp.update_file_hashes({"prr": fake_hash("prr")}, cause=HashUpdateCause.SUCCEEDED)
        step1.mark_completed(StepHash(b"11", None, b"zzz", None), False)
        step2.mark_completed(None, False)
        assert prr.get_state() == FileState.BUILT
        wfp.update_file_hashes({"prr": FileHash.unknown()}, cause=HashUpdateCause.EXTERNAL)
        assert prr.get_state() == FileState.AWAITED
        assert step1.get_state() == StepState.PENDING
        assert step2.get_state() == StepState.PENDING


async def test_externally_updated2(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        (tst,) = declare_static(wfp, wfp.root, ["tst"])
        wfp.define_step(plan, "cat tst", inp_paths=["tst"])
        cat = wfp.find(Step, "cat tst")
        wfp.define_step(plan, "bla1", out_paths=["prr"])
        step1 = wfp.find(Step, "bla1")
        wfp.define_step(plan, "bla2", inp_paths=["prr"])
        step2 = wfp.find(Step, "bla2")

        # Static
        cat.mark_completed(StepHash(b"sfdsafds", None, b"zzz", None), False)
        wfp.update_file_hashes({"tst": FileHash.unknown()}, cause=HashUpdateCause.EXTERNAL)
        assert tst.get_state() == FileState.MISSING
        assert cat.get_state() == StepState.PENDING
        wfp.update_file_hashes({"tst": fake_hash("tst")}, cause=HashUpdateCause.EXTERNAL)
        assert tst.get_state() == FileState.STATIC
        assert cat.get_state() == StepState.PENDING

        # Built
        prr = wfp.find(File, "prr")
        assert prr.get_state() == FileState.AWAITED
        wfp.update_file_hashes({"prr": fake_hash("prr")}, cause=HashUpdateCause.SUCCEEDED)
        step1.mark_completed(StepHash(b"11", None, b"zzz", None), False)
        step2.mark_completed(None, False)
        assert prr.get_state() == FileState.BUILT
        assert step2.get_state() == StepState.FAILED
        wfp.update_file_hashes({"prr": FileHash.unknown()}, cause=HashUpdateCause.EXTERNAL)
        assert prr.get_state() == FileState.AWAITED
        assert step1.get_state() == StepState.PENDING
        assert step2.get_state() == StepState.PENDING


async def test_hash_update_failed(wfp: Workflow):
    """Drive all four `HashUpdateCause.FAILED` rows of `Workflow.update_file_hashes`.

    This cause is normally only reached indirectly, through a failed step in
    `executor.py`, so it has no direct unit coverage otherwise.
    """
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "bla1", out_paths=["out1"])
        out1 = wfp.find(File, "out1")
        assert out1.get_state() == FileState.AWAITED

        # FAILED, AWAITED, known -> OUTDATED
        h = fake_hash("out1")
        wfp.update_file_hashes({"out1": h}, cause=HashUpdateCause.FAILED)
        assert out1.get_state() == FileState.OUTDATED
        assert out1.get_hash() == h

        # FAILED, OUTDATED, known -> OUTDATED
        wfp.update_file_hashes({"out1": h}, cause=HashUpdateCause.FAILED)
        assert out1.get_state() == FileState.OUTDATED
        assert out1.get_hash() == h

        # FAILED, OUTDATED, unknown -> AWAITED
        wfp.update_file_hashes({"out1": FileHash.unknown()}, cause=HashUpdateCause.FAILED)
        assert out1.get_state() == FileState.AWAITED

        # FAILED, AWAITED, unknown -> AWAITED
        wfp.update_file_hashes({"out1": FileHash.unknown()}, cause=HashUpdateCause.FAILED)
        assert out1.get_state() == FileState.AWAITED


async def test_step_recycle(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "echo foo > bar", out_paths=["bar"])
        echo = wfp.find(Step, "echo foo > bar")
        step_hash = StepHash(b"bsfssfdsdfsdfasdfasa", None, b"zzz", None)
        wfp.update_file_hashes({"bar": fake_hash("bar")}, cause=HashUpdateCause.SUCCEEDED)
        echo.mark_completed(step_hash, False)
        hash1 = echo.get_hash()
        assert hash1 is not None

        # Detach and recycle
        echo.detach()
        wfp.define_step(plan, "echo foo > bar", out_paths=["bar"])
        hash2 = echo.get_hash()
        assert hash2 is not None
        assert hash1.inp_digest == hash2.inp_digest
        assert hash1.out_digest == hash2.out_digest


def _get_duration(wfx: Workflow, step: Step) -> float:
    return wfx.db.execute("SELECT duration FROM step WHERE node = ?", (step.i,)).fetchone()[0]


async def test_define_step_duration_default(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "echo")
        step = wfp.find(Step, "echo")
        assert _get_duration(wfp, step) == 1.0


async def test_define_step_duration_given(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "echo", duration=3.5)
        step = wfp.find(Step, "echo")
        assert _get_duration(wfp, step) == 3.5


async def test_define_step_recycle_keeps_duration_by_default(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "echo foo > bar", out_paths=["bar"])
        echo = wfp.find(Step, "echo foo > bar")
        echo.set_duration(7.0)

        # Detach and recycle without an explicit duration: the measured value survives.
        echo.detach()
        wfp.define_step(plan, "echo foo > bar", out_paths=["bar"])
        assert _get_duration(wfp, echo) == 7.0


async def test_define_step_recycle_overrides_duration(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "echo foo > bar", out_paths=["bar"], duration=2.0)
        echo = wfp.find(Step, "echo foo > bar")
        assert _get_duration(wfp, echo) == 2.0

        # Detach and recycle with a new explicit duration: it overrides the old one.
        echo.detach()
        wfp.define_step(plan, "echo foo > bar", out_paths=["bar"], duration=9.0)
        assert _get_duration(wfp, echo) == 9.0


async def test_define_step_invalid_duration(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
    with pytest.raises(sqlite3.IntegrityError):
        async with wfp.db:
            wfp.define_step(plan, "echo", duration=-1.0)


async def test_output_clean_nested(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "echo egg > s/foo/bar/egg", out_paths=["s/foo/bar/egg"])
        step = wfp.find(Step, "echo egg > s/foo/bar/egg")
        wfp.delete_detached()
        f, detached = wfp.find_and_detached(File, "s/foo/bar/egg")
        assert isinstance(f, File)
        assert not detached
        assert f.creator().i == step.i

        step.detach()
        f, detached = wfp.find_and_detached(File, "s/foo/bar/egg")
        assert isinstance(f, File)
        assert detached

        wfp.delete_detached()
        assert wfp.find_and_detached(File, "s/foo/bar/egg") == (None, None)


async def test_clean_multiple_sources(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        (file,) = declare_static(wfp, plan, ["common.txt"])
        wfp.define_step(
            plan, "prog1 common.txt", inp_paths=["common.txt"], out_paths=["output1.txt"]
        )
        step1 = wfp.find(Step, "prog1 common.txt")
        wfp.define_step(
            plan, "prog2 common.txt", inp_paths=["common.txt"], out_paths=["output2.txt"]
        )
        step2 = wfp.find(Step, "prog2 common.txt")
        file.detach()
        wfp.delete_detached()
        assert file.is_detached()
        step1.detach()
        wfp.delete_detached()
        assert file.is_detached()
        step2.detach()
        wfp.delete_detached()
        assert wfp.find_and_detached(File, "common.txt") == (None, None)


async def test_env_vars(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "prog1", env_deps=["name", "other"])
        step = wfp.find(Step, "prog1")
        assert set(step.env_deps(dynamic=False)) == {"name", "other"}
        assert set(step.env_deps(dynamic=True)) == set()
        assert set(step.env_deps()) == {"name", "other"}


async def test_dynamic_env_vars(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "prog1", env_deps=["egg"])
        step = wfp.find(Step, "prog1")
        amend_step(wfp, step, env_deps=["foo", "egg"])
        amend_step(wfp, step, env_deps=["foo", "bar"])
        assert set(step.env_deps(dynamic=False)) == {"egg"}
        assert set(step.env_deps(dynamic=True)) == {"bar", "foo"}
        assert set(step.env_deps()) == {"bar", "egg", "foo"}


async def test_setenv_store_and_update(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "prog1", env_overrides={"FOO": "bar", "BAZ": "1"})
        step = wfp.find(Step, "prog1")
        assert step.get_env_overrides() == {"FOO": "bar", "BAZ": "1"}
        # setset_env_overrides_setenv replaces the overrides (used on detached-step reuse).
        step.set_env_overrides({"FOO": "spam"})
        assert step.get_env_overrides() == {"FOO": "spam"}
        step.set_env_overrides(None)
        assert step.get_env_overrides() == {}


async def test_setenv_amend_ignored(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "prog1", env_overrides={"FOO": "bar"})
        step = wfp.find(Step, "prog1")
        # A variable overridden via env_overrides is not tracked as a dynamic dependency.
        amend_step(wfp, step, env_deps=["FOO", "EGG"])
        assert set(step.env_deps(dynamic=True)) == {"EGG"}
        assert set(step.env_deps()) == {"EGG"}


async def test_setenv_overlap_raises(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
    with pytest.raises(GraphError):
        async with wfp.db:
            wfp.define_step(plan, "prog1", env_deps=["FOO"], env_overrides={"FOO": "bar"})


def test_setenv_affects_inp_digest():
    base = StepHash.from_inp("key", False, {}, {}, False).inp_digest
    one = StepHash.from_inp("key", False, {}, {}, False, {"A": "1"}).inp_digest
    two = StepHash.from_inp("key", False, {}, {}, False, {"A": "2"}).inp_digest
    # An empty override leaves the digest unchanged; different values give different digests.
    assert StepHash.from_inp("key", False, {}, {}, False, {}).inp_digest == base
    assert one != base
    assert one != two


async def test_acyclic_amend_static(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        declare_static(wfp, plan, ["static.txt"])
        amend_step(wfp, plan, inp_paths=["static.txt"])
        assert {r.path for r in plan.inp_paths()} == {"plan.py", "static.txt"}
        assert {r.path for r in plan.static_paths()} == {"static.txt"}


async def test_cyclic_two_steps(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "cat first > second", inp_paths=["first"], out_paths=["second"])
    with pytest.raises(GraphError):
        async with wfp.db:
            wfp.define_step(plan, "cat second > first", inp_paths=["second"], out_paths=["first"])


async def test_cyclic_batch_define_step(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "cat first > second", inp_paths=["first"], out_paths=["second"])
    with pytest.raises(GraphError):
        async with wfp.db:
            # "second" already exists and would close a cycle; "third" is a brand-new file
            # in the SAME batch. This exercises _supply_files' batched cycle check across
            # a mix of new and pre-existing files in one define_step call.
            wfp.define_step(
                plan,
                "cat second third > first",
                inp_paths=["second", "third"],
                out_paths=["first"],
            )
    # Per the transaction-rollback invariant, the whole batch (including the new
    # "third" File node created before the cycle was detected) must be rolled back.
    async with wfp.db:
        assert wfp.find(Step, "cat second third > first") is None
        assert wfp.find(File, "third") is None


async def test_static_tree_basic(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        # Define a step with a detached input
        to_check = wfp.define_step(plan, "cat head/one.txt", inp_paths=["head/one.txt"])
        assert to_check == {}

        # Define static tree and check attributes
    with pytest.raises(ValueError):
        async with wfp.db:
            wfp.register_static_tree(plan, "head*")
    async with wfp.db:
        to_check_h = wfp.register_static_tree(plan, "head")
        to_check_t = wfp.register_static_tree(plan, "tail")
        assert isinstance(wfp.find(StaticTree, "head/"), StaticTree)
        assert isinstance(wfp.find(StaticTree, "tail/"), StaticTree)

        # Validate the to_check result
        assert to_check_h == {"head/one.txt": FileHash.unknown()}
        assert to_check_t == {}
        head1 = wfp.find(File, "head/one.txt")
        assert head1.get_state() == FileState.UNCONFIRMED

        # Check if head_1.txt is static after confirming
        wfp.update_file_hashes(
            {"head/one.txt": fake_hash("head/one.txt")}, cause=HashUpdateCause.CONFIRMED
        )
        assert head1.get_state() == FileState.STATIC

        # Use static tree after it is added
        to_check = wfp.define_step(plan, "cat tail/one.txt", inp_paths=["tail/one.txt"])
        assert to_check == {"tail/one.txt": FileHash.unknown()}
        tail1 = wfp.find(File, "tail/one.txt")
        assert tail1.get_state() == FileState.UNCONFIRMED
    async with wfp.db:
        # Confirming with an unknown hash means the file was checked and found absent.
        wfp.update_file_hashes(to_check, cause=HashUpdateCause.CONFIRMED)
        assert tail1.get_state() == FileState.MISSING
    async with wfp.db:
        # The file appears later, e.g. detected by the watcher.
        wfp.update_file_hashes(
            {"tail/one.txt": fake_hash("tail/one.txt")}, cause=HashUpdateCause.EXTERNAL
        )
        assert tail1.get_state() == FileState.STATIC


async def test_static_tree_clean(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        to_check = wfp.register_static_tree(plan, "static")
        assert len(to_check) == 0
        inp_paths = ["static/foo/bar.txt"]
        to_check = wfp.define_step(plan, "cat static/foo/bar.txt", inp_paths=inp_paths)
        assert to_check == {"static/foo/bar.txt": FileHash.unknown()}
        wfp.update_file_hashes(
            {"static/foo/bar.txt": fake_hash("static/foo/bar.txt")},
            cause=HashUpdateCause.CONFIRMED,
        )
        step = wfp.find(Step, "cat static/foo/bar.txt")

        # Check effect of defining the step on the static tree
        assert wfp.find(File, "static/foo/bar.txt").get_state() == FileState.STATIC

        # Simulate the execution of the steps
        plan.mark_completed(StepHash(b"sthp", None, b"zzz", None), False)
        step.mark_completed(StepHash(b"sths", None, b"zzz", None), False)

        # Check the hashes
        assert plan.get_hash().inp_digest == b"sthp"
        assert step.get_hash().inp_digest == b"sths"

        # Detach the step, manually outdate it, clean and check result
        step.detach()
        wfp.delete_detached()
        sr = wfp.find(StaticTree, "static/")
        assert sr.creator().i == plan.i
        assert not step.in_graph()
        assert wfp.find_and_detached(File, "static") == (None, None)
        assert wfp.find_and_detached(File, "static/") == (None, None)
        assert wfp.find_and_detached(File, "static/foo") == (None, None)
        assert wfp.find_and_detached(File, "static/foo/") == (None, None)
        assert wfp.find_and_detached(File, "static/foo/bar.txt") == (None, None)

        # make the plan pending
        wfp.mark_step_pending(plan)
        assert not sr.is_detached()
        assert plan.get_state() == StepState.PENDING


async def test_clean_cycle_invalidates_hash(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        declare_static(wfp, plan, ["sub/plan.py"])
        wfp.define_step(plan, "./plan.py", inp_paths=["sub/plan.py"], workdir="sub")
        sub = wfp.find(Step, "./plan.py  # wd=sub")

        # The sub plan declares data.txt static and then uses it as an input of itself,
        # a cycle in the combined provenance and dependency graph.
        declare_static(wfp, sub, ["sub/data.txt"])
        amend_step(wfp, sub, inp_paths=["sub/data.txt"])
        wfp.define_step(
            sub,
            "cp data.txt copy.txt",
            inp_paths=["data.txt"],
            out_paths=["copy.txt"],
            workdir="sub",
        )
        copy = wfp.find(Step, "cp data.txt copy.txt  # wd=sub")
        sub.mark_completed(StepHash(b"sths", None, b"zzz", None), False)
        assert sub.get_hash() is not None

        # Detach the sub plan step and clean up. The cycle survives, the rest does not.
        sub.detach()
        wfp.delete_detached()
        assert sub.in_graph()
        assert wfp.find(File, "sub/data.txt").in_graph()
        assert not copy.in_graph()

        # Because the sub plan step lost a product, it must not be skipped when recycled.
        assert sub.get_hash() is None


async def test_static_tree_subdir(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.register_static_tree(plan, "static/sub")
    # Becoming the parent of an existing tree still raises, regardless of creator.
    with pytest.raises(GraphError):
        async with wfp.db:
            wfp.register_static_tree(plan, "static")
    # A subdirectory of the same creator's own tree is a no-op, not a rejection.
    async with wfp.db:
        assert wfp.register_static_tree(plan, "static/sub/dir") == {}
        assert wfp.find(StaticTree, "static/sub/dir/") is None


async def test_static_tree_then_static_file_hands_over(wfp: Workflow):
    """Declaring a file already owned by the same creator's static tree hands it over.

    The tree was declared first, so `declare_static_files` finds it immediately: the
    file is declared eagerly under the tree, rather than waiting for lazy adoption
    through a step input.
    """
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.register_static_tree(plan, "static")
        to_check = wfp.declare_static_files(plan, ["static/README.md"])
        assert to_check == {"static/README.md": FileHash.unknown()}
        readme = wfp.find(File, "static/README.md")
        assert readme.creator() == wfp.find(StaticTree, "static/")

        wfp.update_file_hashes(
            {"static/README.md": fake_hash("static/README.md")}, cause=HashUpdateCause.CONFIRMED
        )
        assert readme.get_state() == FileState.STATIC

        to_check = wfp.define_step(plan, "cat static/README.md", inp_paths=["static/README.md"])
        assert to_check == {}
        assert readme.creator() == wfp.find(StaticTree, "static/")


async def test_static_tree_then_static_file_raises_other_creator(wfp: Workflow):
    """The tree-first order raises for a foreign creator.

    This is the centrepiece of the phase: previously this order was a silent no-op
    regardless of which step declared the file, so whether an independent plan's build
    succeeded could depend on which of two plans ran first. Now both orders raise; see
    `test_static_file_then_static_tree_raises_other_creator` for the reverse order and
    `test_static_tree_conflict_same_message_both_orders` for the message equality.
    """
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "sub")
        sub = wfp.find(Step, "sub")
        wfp.register_static_tree(plan, "data")
    with pytest.raises(
        GraphError, match=re.escape(_static_tree_file_message("data/", "data/foo.txt"))
    ):
        async with wfp.db:
            wfp.declare_static_files(sub, ["data/foo.txt"])
    async with wfp.db:
        assert wfp.find(File, "data/foo.txt") is None


async def test_static_tree_conflict_same_message_both_orders(wfp: Workflow):
    """Tree-first and file-first raise byte-identical text for a foreign creator.

    A second, independent workflow is used for the file-first order, mirroring
    `test_eager_checks_agree_on_message_text`: reusing `wfp` would leave the first
    raise's partial state contaminating the comparison.
    """
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "sub")
        sub = wfp.find(Step, "sub")
        wfp.register_static_tree(plan, "data")
    with pytest.raises(GraphError) as excinfo_a:
        async with wfp.db:
            wfp.declare_static_files(sub, ["data/foo.txt"])

    dir_queue = asyncio.Queue()
    with DBSession.open(":memory:") as db:
        wfp_b = Workflow(db, create_parent_dirs=False, dir_queue=dir_queue)
        await wfp_b.initialize()
        async with db:
            declare_static(wfp_b, wfp_b.root, ["plan.py"])
            wfp_b.define_step(wfp_b.root, "./plan.py", inp_paths=["plan.py"], need=Need.PLAN)
            plan_b = wfp_b.find(Step, "./plan.py")
            wfp_b.define_step(plan_b, "sub")
            sub_b = wfp_b.find(Step, "sub")
            declare_static(wfp_b, sub_b, ["data/foo.txt"])
            with pytest.raises(GraphError) as excinfo_b:
                wfp_b.register_static_tree(plan_b, "data")

    assert str(excinfo_a.value) == str(excinfo_b.value)


async def test_static_tree_declare_static_files_queues_parent_dir(wfp: Workflow):
    """A tree-covered path declared through `declare_static_files` still watches its parent.

    The file is now declared eagerly under the tree by `_declare_file`, rather than
    filtered out and left for lazy adoption; `_declare_file` itself calls
    `watch_dir`, so the parent directory ends up watched either way.
    """
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.register_static_tree(plan, "static")
        while not wfp.dir_queue.empty():
            wfp.dir_queue.get_nowait()

        to_check = wfp.declare_static_files(plan, ["static/sub/README.md"])

        assert to_check == {"static/sub/README.md": FileHash.unknown()}
        assert wfp.find(File, "static/sub/README.md") is not None
        assert wfp.dir_queue.get_nowait() == "static/sub"
        assert wfp.dir_queue.empty()


async def test_static_tree_output(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.register_static_tree(plan, "static")
    with pytest.raises(
        GraphError, match=re.escape(_static_tree_product_message("static/", "static/README.md"))
    ) as excinfo:
        async with wfp.db:
            wfp.define_step(plan, "echo foo > static/README.md", out_paths=["static/README.md"])
    assert "glob()" not in str(excinfo.value)


async def test_static_tree_volatile(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.register_static_tree(plan, "static")
    with pytest.raises(
        GraphError, match=re.escape(_static_tree_product_message("static/", "static/README.md"))
    ) as excinfo:
        async with wfp.db:
            wfp.define_step(plan, "echo foo > static/README.md", vol_paths=["static/README.md"])
    assert "glob()" not in str(excinfo.value)


async def test_static_tree_output_same_creator_still_raises(wfp: Workflow):
    """A build product inside a tree still raises even for the tree's own creator.

    The same-creator no-op is specific to static declarations
    (see `test_static_tree_same_creator_file_and_subdir_both_no_op`); `_declare_file`'s
    product branch has no such exemption. `define_step`'s out_paths always belong to a
    freshly created child step, which can never equal an existing tree's creator, so
    calling `_declare_file` directly with the tree's own creator is the only way to
    exercise this case.
    """
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.register_static_tree(plan, "static")
    with pytest.raises(GraphError):
        async with wfp.db:
            wfp._declare_file(plan, "static/out.txt", FileState.AWAITED)


async def test_static_tree_product_message_both_orders(wfp: Workflow):
    """Tree-then-output and output-then-tree raise byte-identical text.

    Unlike the static-file collision, a build product has no same-creator exemption
    (`test_static_tree_output_same_creator_still_raises`), so both directions can use
    the same creator and still raise.
    """
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.register_static_tree(plan, "data")
    with pytest.raises(GraphError) as excinfo_a:
        async with wfp.db:
            wfp.define_step(plan, "touch data/out.txt", out_paths=["data/out.txt"])

    dir_queue = asyncio.Queue()
    with DBSession.open(":memory:") as db:
        wfp_b = Workflow(db, create_parent_dirs=False, dir_queue=dir_queue)
        await wfp_b.initialize()
        async with db:
            declare_static(wfp_b, wfp_b.root, ["plan.py"])
            wfp_b.define_step(wfp_b.root, "./plan.py", inp_paths=["plan.py"], need=Need.PLAN)
            plan_b = wfp_b.find(Step, "./plan.py")
            wfp_b.define_step(plan_b, "touch data/out.txt", out_paths=["data/out.txt"])
            with pytest.raises(GraphError) as excinfo_b:
                wfp_b.register_static_tree(plan_b, "data")

    assert str(excinfo_a.value) == str(excinfo_b.value)


async def test_orhphaned_static_tree(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.register_static_tree(plan, "part_a")
        wfp.register_static_tree(plan, "part_b")
        wfp.find(StaticTree, "part_a/").detach()
        to_check = wfp.define_step(plan, "prog", inp_paths=["part_a/README.md", "part_b/README.md"])
        assert to_check == {"part_b/README.md": FileHash.unknown()}


async def test_static_tree_amend_inp(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.register_static_tree(plan, "static")
        to_check = wfp.define_step(
            plan, "prog", inp_paths=["static/initial.md", "other/initial.md"]
        )
        assert to_check == {"static/initial.md": FileHash.unknown()}
        prog = wfp.find(Step, "prog")
        carry_on, to_check = _amend(wfp, prog, inp_paths=["static/other.md"])
        assert carry_on
        assert to_check == {"static/other.md": FileHash.unknown()}
        carry_on, to_check = _amend(wfp, prog, inp_paths=["static/dynamic.md", "other/dynamic.md"])
        assert not carry_on
        assert to_check == {"static/dynamic.md": FileHash.unknown()}


async def test_static_tree_amend_out(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.register_static_tree(plan, "data")
        to_check = wfp.define_step(plan, "prog", inp_paths=["data/somefile.txt"])
        assert to_check == {"data/somefile.txt": FileHash.unknown()}
        prog = wfp.find(Step, "prog")
    with pytest.raises(GraphError):
        async with wfp.db:
            amend_step(wfp, prog, vol_paths=["data/vol_dynamic/vol.txt"])
    with pytest.raises(GraphError):
        async with wfp.db:
            amend_step(wfp, prog, out_paths=["data/out_dynamic/out.txt"])


async def test_static_tree_recursive(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.register_static_tree(plan, "data")
        to_check = wfp.define_step(
            plan, "prog", inp_paths=["data/foo/a/bar.txt", "data/foo/b/egg.txt"]
        )
        assert to_check == {
            "data/foo/a/bar.txt": FileHash.unknown(),
            "data/foo/b/egg.txt": FileHash.unknown(),
        }


async def test_static_tree_race_condition(wfp: Workflow):
    """Two steps race to be the first to use the same static-tree file as input.

    Both `define_step` calls happen before either confirmation is processed, so both
    are told to check and confirm the file. The second confirmation to arrive used to
    crash with `Unexpected file hash update: cause=CONFIRMED ... state=FileState.STATIC`.
    """
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.register_static_tree(plan, "data")
        to_check_a = wfp.define_step(plan, "prog_a", inp_paths=["data/foo.txt"])
        to_check_b = wfp.define_step(plan, "prog_b", inp_paths=["data/foo.txt"])
        assert to_check_a == {"data/foo.txt": FileHash.unknown()}
        assert to_check_b == {"data/foo.txt": FileHash.unknown()}

        # Client A confirms first: UNCONFIRMED -> STATIC.
        wfp.update_file_hashes(
            {"data/foo.txt": fake_hash("data/foo.txt")}, cause=HashUpdateCause.CONFIRMED
        )
        foo = wfp.find(File, "data/foo.txt")
        assert foo.get_state() == FileState.STATIC

        # Client B's confirmation for the same path arrives second and must not crash.
        wfp.update_file_hashes(
            {"data/foo.txt": fake_hash("data/foo.txt")}, cause=HashUpdateCause.CONFIRMED
        )
        assert foo.get_state() == FileState.STATIC


async def test_static_tree_same_creator_file_and_subdir_both_no_op(wfp: Workflow):
    """The same-creator rule is uniform across files and subdirectories.

    `static("data/")` followed by `static("data/sub/")` or by `static("data/foo.txt")`
    are both no-ops for the tree's own creator:
    `test_register_static_tree_same_creator_subdirectory` already covers the directory
    case; this test exists so the file case is visibly the same rule, not a separate
    exception.
    """
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.register_static_tree(plan, "data")
        assert wfp.register_static_tree(plan, "data/sub") == {}
        assert wfp.find(StaticTree, "data/sub/") is None
        to_check = wfp.declare_static_files(plan, ["data/foo.txt"])
        assert to_check == {"data/foo.txt": FileHash.unknown()}
        assert wfp.find(File, "data/foo.txt").creator() == wfp.find(StaticTree, "data/")


async def test_static_tree_glob_owns_nothing(wfp: Workflow):
    """A `glob()` pattern matching inside another step's static tree is not a collision.

    After Phase 2, `register_nglob` no longer declares its matches, so there is nothing
    for the tree to conflict with (README open question 3): the pattern is accepted
    even though `sub`, not `plan`, registers it.
    """
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "sub")
        sub = wfp.find(Step, "sub")
        wfp.register_static_tree(plan, "data")
        ng = NamedGlob("data/*.txt")
        ng.extend(["data/foo.txt"])
        wfp.register_nglob(sub, ng)


async def test_define_step_reqdir_out_path(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "echo", out_paths=["sub/dir/out"])
        reqdir, detached = wfp.find_and_detached(File, "sub/dir")
        assert reqdir is None
        assert detached is None


async def test_define_step_reqdir_vol_path(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "echo", vol_paths=["sub/dir/vol"])
        reqdir, detached = wfp.find_and_detached(File, "sub/dir")
        assert reqdir is None
        assert detached is None


async def test_define_step_reqdir_workdir(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "echo", workdir="sub/dir")
        echo = wfp.find(Step, "echo  # wd=sub/dir")
        command, workdir = echo.command_and_workdir
        assert command == "echo"
        assert workdir == Path("sub/dir")
        assert isinstance(workdir, Path)
        reqdir, detached = wfp.find_and_detached(File, "sub/dir")
        assert reqdir is None
        assert detached is None


async def test_amend_step_reqdir_out_path(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "echo")
        step = wfp.find(Step, "echo")
        amend_step(wfp, step, out_paths=["sub/dir/out"])
        reqdir, detached = wfp.find_and_detached(File, "sub/dir")
        assert reqdir is None
        assert detached is None


async def test_amend_step_reqdir_vol_path(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "echo")
        step = wfp.find(Step, "echo")
        amend_step(wfp, step, vol_paths=["sub/dir/vol"])
        reqdir, detached = wfp.find_and_detached(File, "sub/dir")
        assert reqdir is None
        assert detached is None


async def test_define_step_directory_input_disallowed(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        with pytest.raises(GraphError, match="Directory inputs are not supported"):
            wfp.define_step(plan, "echo", inp_paths=["sub/"])


async def test_inp_paths(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "script", inp_paths=["foo"])
        step = wfp.find(Step, "script")
        assert {r.path for r in step.inp_paths()} == set()
        assert {(r.path, r.detached) for r in step.inp_paths(include_detached=True)} == {
            ("foo", True)
        }
        assert list(step.inp_paths()) == []
        assert {(r.path, r.state, r.detached) for r in step.inp_paths(include_detached=True)} == {
            ("foo", FileState.AWAITED, True),
        }
        assert list(step.inp_paths()) == []


async def test_out_paths(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "script", out_paths=["foo", "bar"])
        step = wfp.find(Step, "script")
        wfp.update_file_hashes({"bar": fake_hash("bar")}, cause=HashUpdateCause.SUCCEEDED)
        assert {r.path for r in step.out_paths()} == {"bar", "foo"}
        assert {(r.path, r.state) for r in step.out_paths()} == {
            ("bar", FileState.BUILT),
            ("foo", FileState.AWAITED),
        }
        assert sorted((r.path, r.hash) for r in step.out_paths()) == [
            ("bar", fake_hash("bar")),
            ("foo", FileHash.unknown()),
        ]
        assert sorted((r.path, r.state, r.hash) for r in step.out_paths()) == [
            ("bar", FileState.BUILT, fake_hash("bar")),
            ("foo", FileState.AWAITED, FileHash.unknown()),
        ]


async def test_vol_paths(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "script", vol_paths=["foo", "bar"])
        step = wfp.find(Step, "script")
        assert {r.path for r in step.vol_paths()} == {"bar", "foo"}
        assert sorted((r.path, r.hash) for r in step.vol_paths()) == [
            ("bar", FileHash.unknown()),
            ("foo", FileHash.unknown()),
        ]


async def test_static_missing_paths(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "script")
        step = wfp.find(Step, "script")
        declare_static(wfp, step, ["foo", "bar", "zzz"])
        wfp.find(File, "zzz").set_state(FileState.MISSING)
        assert {r.path for r in step.static_paths()} == {"bar", "foo"}
        assert {r.path for r in step.missing_paths()} == {"zzz"}
        assert sorted((r.path, r.hash) for r in step.static_paths()) == [
            ("bar", fake_hash("bar")),
            ("foo", fake_hash("foo")),
        ]
        assert [(r.path, r.hash) for r in step.missing_paths()] == [("zzz", FileHash.unknown())]


async def test_skip_amend_detached_inputs(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "prog", out_paths=["bar"])
        step = wfp.find(Step, "prog")
        (foo1,) = declare_static(wfp, plan, ["foo"])

        # Simulate running the step, which amends a few things.
        amend_step(wfp, step, inp_paths=["foo"], env_deps=["AAA"], vol_paths=["bbb"])
        assert {(r.path, r.detached, r.dynamic) for r in step.inp_paths(include_detached=True)} == {
            ("foo", False, True)
        }
        assert set(step.env_deps()) == {"AAA"}
        assert {(r.path, r.detached, r.dynamic) for r in step.out_paths(include_detached=True)} == {
            ("bar", False, False),
        }
        assert {(r.path, r.detached, r.dynamic) for r in step.vol_paths(include_detached=True)} == {
            ("bbb", False, True),
        }
        wfp.update_file_hashes({"bar": fake_hash("bar")}, cause=HashUpdateCause.SUCCEEDED)
        step.mark_completed(StepHash(b"step_ok", None, b"zzz", None), False)
        assert step.get_state() == StepState.SUCCEEDED
        assert step.get_hash() is not None

        # Detach the static input.
        foo1.detach()
        assert foo1.is_detached()
        # Dynamic info is not removed
        assert {(r.path, r.detached, r.dynamic) for r in step.inp_paths(include_detached=True)} == {
            ("foo", True, True)
        }
        assert set(step.env_deps()) == {"AAA"}
        assert {(r.path, r.detached, r.dynamic) for r in step.out_paths(include_detached=True)} == {
            ("bar", False, False),
        }
        assert {(r.path, r.detached, r.dynamic) for r in step.vol_paths(include_detached=True)} == {
            ("bbb", False, True),
        }

        # Detach step1
        step.detach()
        assert step.in_graph()
        assert step.is_detached()
        assert step.get_hash() is not None

        # Redefine the step in exactly the same way
        (foo2,) = declare_static(wfp, plan, ["foo"])
        assert foo1 == foo2
        wfp.define_step(plan, "prog", out_paths=["bar"])
        assert not step.is_detached()
        assert {r.path for r in step.inp_paths()} == {"foo"}
        assert {(r.path, r.detached) for r in step.inp_paths(include_detached=True)} == {
            ("foo", False)
        }
        assert {r.path for r in step.out_paths()} == {"bar"}
        # Note that dynamic info is removed when inputs of a step are detached.
        assert {r.path for r in step.vol_paths()} == {"bbb"}

        # Check that dynamic info is back and hash is still in place
        assert step.get_hash() is not None


async def test_define_step_out_nested(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "script", out_paths=["sub/foo/bar"])
        step = wfp.find(Step, "script")
        assert {r.path for r in step.inp_paths()} == set()
        assert {r.path for r in step.out_paths()} == {"sub/foo/bar"}


async def test_define_step_vol_nested(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "script", vol_paths=["sub/foo/bar"])
        step = wfp.find(Step, "script")
        assert {r.path for r in step.inp_paths()} == set()
        assert {r.path for r in step.out_paths()} == set()
        assert {r.path for r in step.vol_paths()} == {"sub/foo/bar"}


async def test_amend_step_out_nested(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "script")
        step = wfp.find(Step, "script")
        amend_step(wfp, step, out_paths=["sub/foo/bar"])
        assert {r.path for r in step.inp_paths()} == set()
        assert {r.path for r in step.out_paths()} == {"sub/foo/bar"}


async def test_amend_step_vol_nested(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "script")
        step = wfp.find(Step, "script")
        amend_step(wfp, step, vol_paths=["sub/foo/bar"])
        assert {r.path for r in step.inp_paths()} == set()
        assert {r.path for r in step.out_paths()} == set()
        assert {r.path for r in step.vol_paths()} == {"sub/foo/bar"}


async def test_step_static_tree(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        assert wfp.register_static_tree(plan, "sub") == {}
        inp_paths = ["test.png", "test.txt", "other.txt", "sub/boom.txt", "sub/README.md"]
        to_check = wfp.define_step(plan, "prog", inp_paths=inp_paths)
        assert to_check == {"sub/README.md": FileHash.unknown(), "sub/boom.txt": FileHash.unknown()}

        # Check file nodes
        for path in "test.png", "test.txt", "other.txt":
            file, detached = wfp.find_and_detached(File, path)
            assert detached
            assert file.get_state() == FileState.AWAITED
        for path in "sub/boom.txt", "sub/README.md":
            file, detached = wfp.find_and_detached(File, path)
            assert not detached
            assert file.get_state() == FileState.UNCONFIRMED


async def test_confirm_unconfirmed(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "cat ${inp}", inp_paths=["test.txt"])
        to_check = wfp.declare_static_files(plan, ["test.txt", "other.txt"])
        assert to_check == {"other.txt": FileHash.unknown(), "test.txt": FileHash.unknown()}
        # static other.txt
        assert wfp.find(File, "other.txt").get_state() == FileState.UNCONFIRMED
        wfp.update_file_hashes(
            {"other.txt": fake_hash("other.txt")}, cause=HashUpdateCause.CONFIRMED
        )
        assert wfp.find(File, "other.txt").get_state() == FileState.STATIC
        # static test.txt
        assert wfp.find(File, "test.txt").get_state() == FileState.UNCONFIRMED
        wfp.update_file_hashes({"test.txt": fake_hash("test.txt")}, cause=HashUpdateCause.CONFIRMED)
        assert wfp.find(File, "test.txt").get_state() == FileState.STATIC


async def test_recycle_preserves_hash_across_rerun(wfp: Workflow):
    """A redeclared static file must keep its old hash, not have it nulled by recycling."""
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        (foo,) = declare_static(wfp, plan, ["foo.txt"])
        old_hash = foo.get_hash()
        assert not old_hash.is_unknown

        # Simulate a step rerun: detach the previously declared static file.
        foo.detach()
        assert wfp.find_and_detached(File, "foo.txt") == (foo, True)

        # Redeclare the same path: the recycled node must keep its old hash, not lose it
        # to the file_clear_hash trigger (which only fires for MISSING/AWAITED/VOLATILE).
        to_check = wfp.declare_static_files(plan, ["foo.txt"])
        assert to_check == {"foo.txt": old_hash}
        assert foo.get_state() == FileState.UNCONFIRMED
        assert foo.get_hash() == old_hash

        # Confirming with the same (unchanged) hash must still flip the state: the second
        # RPC call always fires client-side, even when regen() finds no change.
        wfp.update_file_hashes({"foo.txt": old_hash}, cause=HashUpdateCause.CONFIRMED)
        assert foo.get_state() == FileState.STATIC
        assert foo.get_hash() == old_hash


async def test_confirm_unconfirmed_absent(wfp: Workflow):
    """Confirming an UNCONFIRMED file as absent must transition it to MISSING."""
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        to_check = wfp.declare_static_files(plan, ["ghost.txt"])
        assert to_check == {"ghost.txt": FileHash.unknown()}
        ghost = wfp.find(File, "ghost.txt")
        assert ghost.get_state() == FileState.UNCONFIRMED
        wfp.update_file_hashes({"ghost.txt": FileHash.unknown()}, cause=HashUpdateCause.CONFIRMED)
        assert ghost.get_state() == FileState.MISSING


async def test_confirm_missing_duplicate(wfp: Workflow):
    """A second, duplicate absent-confirmation for an already-MISSING file must not crash."""
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.declare_static_files(plan, ["ghost.txt"])
        wfp.update_file_hashes({"ghost.txt": FileHash.unknown()}, cause=HashUpdateCause.CONFIRMED)
        ghost = wfp.find(File, "ghost.txt")
        assert ghost.get_state() == FileState.MISSING

        # A second confirmation for the same (still absent) path must be a harmless no-op.
        wfp.update_file_hashes({"ghost.txt": FileHash.unknown()}, cause=HashUpdateCause.CONFIRMED)
        assert ghost.get_state() == FileState.MISSING


async def test_confirm_missing_then_present(wfp: Workflow):
    """A confirmation finding the file present after a prior absent-confirmation must not crash."""
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.declare_static_files(plan, ["ghost.txt"])
        wfp.update_file_hashes({"ghost.txt": FileHash.unknown()}, cause=HashUpdateCause.CONFIRMED)
        ghost = wfp.find(File, "ghost.txt")
        assert ghost.get_state() == FileState.MISSING

        # A later confirmation for the same path that now finds it present must win.
        wfp.update_file_hashes(
            {"ghost.txt": fake_hash("ghost.txt")}, cause=HashUpdateCause.CONFIRMED
        )
        assert ghost.get_state() == FileState.STATIC
        assert ghost.get_hash() == fake_hash("ghost.txt")


async def test_confirm_static_then_absent(wfp: Workflow):
    """A confirmation finding the file absent after a prior present-confirmation must not crash."""
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        declare_static(wfp, plan, ["foo.txt"])
        foo = wfp.find(File, "foo.txt")
        assert foo.get_state() == FileState.STATIC

        # A later confirmation for the same path that now finds it absent must win.
        wfp.update_file_hashes({"foo.txt": FileHash.unknown()}, cause=HashUpdateCause.CONFIRMED)
        assert foo.get_state() == FileState.MISSING
        assert foo.get_hash() == FileHash.unknown()


async def test_hash_update_external_unconfirmed(wfp: Workflow):
    """Drive the (EXTERNAL, UNCONFIRMED, True/False) rows, a defensive fallback.

    `scan_file_changes` (Phase 2) resolves stray UNCONFIRMED rows via CONFIRMED at startup,
    so this EXTERNAL/UNCONFIRMED path should no longer be reachable there in practice.
    It is kept as a defensive fallback (see the comment on these entries in
    `_HASH_TRANSITIONS`) in case a non-detached UNCONFIRMED file ever survives into a
    watch phase, whose own hashing loop uses EXTERNAL.
    """
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.declare_static_files(plan, ["a.txt", "b.txt"])
        a = wfp.find(File, "a.txt")
        b = wfp.find(File, "b.txt")
        assert a.get_state() == FileState.UNCONFIRMED
        assert b.get_state() == FileState.UNCONFIRMED

        wfp.update_file_hashes(
            {"a.txt": fake_hash("a.txt"), "b.txt": FileHash.unknown()},
            cause=HashUpdateCause.EXTERNAL,
        )
        assert a.get_state() == FileState.STATIC
        assert b.get_state() == FileState.MISSING


async def test_step_completed_succeeds_with_unconfirmed_product(wfp: Workflow):
    """Step.mark_completed() must accept a step whose declared static file is still UNCONFIRMED.

    Since hash confirmation is fire-and-forget (Phase 3), this is now a normal state, not a
    protocol violation: the confirming hash job may still be queued or in flight when the
    declaring step completes. A consumer of the file cannot become runnable before the hash
    job resolves it (the scheduler's UNCONFIRMED-is-not-ready gate), and a stray UNCONFIRMED
    row left behind by a crash is resolved by the next startup scan.
    """
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.declare_static_files(plan, ["ghost.txt"])
        assert wfp.find(File, "ghost.txt").get_state() == FileState.UNCONFIRMED
    async with wfp.db:
        plan.mark_completed(StepHash(b"p" * 32, None, b"p" * 32, None), False)
        assert plan.get_state() == StepState.SUCCEEDED
        assert wfp.find(File, "ghost.txt").get_state() == FileState.UNCONFIRMED


async def test_step_completed_does_not_raise_on_unconfirmed_product_when_failed(wfp: Workflow):
    """Step.mark_completed(None, False) must not raise even with a pending UNCONFIRMED product.

    A step process killed mid-run (e.g. a Ctrl-C while some other file was still being
    hashed) reaches `completed(None, False)` with a still-UNCONFIRMED declared file. This is
    expected on the failed path, not a protocol violation: `reset_for_rerun()` detaches the
    leftover cleanly on the next run.
    """
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.declare_static_files(plan, ["ghost.txt"])
        assert wfp.find(File, "ghost.txt").get_state() == FileState.UNCONFIRMED
    async with wfp.db:
        plan.mark_completed(None, False)
        assert plan.get_state() == StepState.FAILED


async def test_step_completed_ignores_detached_unconfirmed_product(wfp: Workflow):
    """Step.mark_completed() must ignore a detached UNCONFIRMED product, even on success.

    A detached product is no longer this step's responsibility, e.g. because it was left
    behind by a killed run and then orphaned when the plan.py source line that declared it
    was edited away.
    """
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "sub")
        sub = wfp.find(Step, "sub")
        wfp.declare_static_files(sub, ["ghost.txt"])
        assert wfp.find(File, "ghost.txt").get_state() == FileState.UNCONFIRMED
        sub.reset_for_rerun()
    async with wfp.db:
        sub.mark_completed(StepHash(b"s" * 32, None, b"s" * 32, None), False)
        assert sub.get_state() == StepState.SUCCEEDED


async def test_reset_for_rerun_detaches_unconfirmed(wfp: Workflow):
    """`reset_for_rerun()` must detach a still-UNCONFIRMED static file declaration."""
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "sub")
        sub = wfp.find(Step, "sub")
        wfp.declare_static_files(sub, ["ghost.txt"])
        ghost = wfp.find(File, "ghost.txt")
        assert ghost.get_state() == FileState.UNCONFIRMED
        sub.reset_for_rerun()
        assert wfp.find_and_detached(File, "ghost.txt") == (ghost, True)


async def test_register_static_tree_rejects_attached_unconfirmed_or_missing(wfp: Workflow):
    """A static tree cannot be declared over a file already attached to another creator.

    This holds regardless of the file's state: an UNCONFIRMED or MISSING file declared by
    another creator blocks the tree exactly like a STATIC or BUILT one would, per the rule
    that a static tree is the sole owner of the files under it. The two declarations here
    already come from different creators (`sub` and `plan`), which is what keeps this a
    rejection rather than the hand-over case covered by
    `test_static_file_then_static_tree_hands_over`.
    """
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "sub")
        sub = wfp.find(Step, "sub")

        # An UNCONFIRMED file declared (but not yet confirmed) by another creator.
        wfp.declare_static_files(sub, ["data/unconfirmed.txt"])
        unconfirmed = wfp.find(File, "data/unconfirmed.txt")
        assert unconfirmed.get_state() == FileState.UNCONFIRMED

    with pytest.raises(
        GraphError,
        match=re.escape(_static_tree_file_message("data/", "data/unconfirmed.txt")),
    ):
        async with wfp.db:
            wfp.register_static_tree(plan, "data")
    async with wfp.db:
        assert unconfirmed.creator() == sub
        assert not unconfirmed.is_detached()
        assert wfp.find(StaticTree, "data/") is None

        # A MISSING (confirmed absent) file declared by another creator blocks it too.
        wfp.declare_static_files(sub, ["other/missing.txt"])
        wfp.update_file_hashes(
            {"other/missing.txt": FileHash.unknown()}, cause=HashUpdateCause.CONFIRMED
        )
        missing = wfp.find(File, "other/missing.txt")
        assert missing.get_state() == FileState.MISSING

    with pytest.raises(
        GraphError, match=re.escape(_static_tree_file_message("other/", "other/missing.txt"))
    ):
        async with wfp.db:
            wfp.register_static_tree(plan, "other")
    async with wfp.db:
        assert missing.creator() == sub
        assert not missing.is_detached()
        assert wfp.find(StaticTree, "other/") is None


async def test_static_file_then_static_tree_hands_over(wfp: Workflow):
    """The file-first order hands the file over to the tree, for the same creator.

    Four things must survive the hand-over: the new creator, the consuming step's
    dependency edge, the stored hash, and — the one a `Trellis.create()`-based
    implementation would get wrong — the declaring step's own hash, since the tree
    registration must not look like a recycle of the file's previous creator
    (see the "bypassing `Trellis.create()`" note at `register_static_tree`).
    """
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        (foo,) = declare_static(wfp, plan, ["data/foo.txt"])
        foo_hash = foo.get_hash()
        wfp.define_step(plan, "prog", inp_paths=["data/foo.txt"])
        prog = wfp.find(Step, "prog")
        plan.mark_completed(StepHash(b"p" * 32, None, b"p" * 32, None), False)
        assert plan.get_hash() is not None

        wfp.register_static_tree(plan, "data")

        assert foo.creator() == wfp.find(StaticTree, "data/")
        assert list(foo.sinks()) == [prog]
        assert wfp.get_file_hashes(["data/foo.txt"]) == {"data/foo.txt": foo_hash}
        assert plan.get_hash() is not None


async def test_static_file_then_static_tree_raises_other_creator(wfp: Workflow):
    """The file-first order raises for a foreign creator, mirroring the tree-first case."""
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "sub")
        sub = wfp.find(Step, "sub")
        declare_static(wfp, sub, ["data/foo.txt"])
    with pytest.raises(
        GraphError, match=re.escape(_static_tree_file_message("data/", "data/foo.txt"))
    ):
        async with wfp.db:
            wfp.register_static_tree(plan, "data")
    async with wfp.db:
        assert wfp.find(File, "data/foo.txt").creator() == sub
        assert wfp.find(StaticTree, "data/") is None


async def test_static_tree_handover_multiple_files(wfp: Workflow):
    """Every matching file is handed over in one `register_static_tree` call.

    The hand-over loops over every attached file under the new tree's path; a `LIMIT 1`
    left in the query by accident would silently hand over only the first match.
    """
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        declare_static(wfp, plan, ["data/a.txt", "data/b.txt", "data/c.txt"])
        wfp.register_static_tree(plan, "data")
        tree = wfp.find(StaticTree, "data/")
        for name in ("a", "b", "c"):
            assert wfp.find(File, f"data/{name}.txt").creator() == tree


async def test_register_static_tree_adopts_detached_file(wfp: Workflow):
    """A detached file left behind by a removed creator is silently adopted (restart case).

    Unlike an attached file, a detached one has no live creator to conflict with, so
    declaring the tree over it recycles the node instead of raising.
    """
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        (foo,) = declare_static(wfp, plan, ["data/foo.txt"])
        assert foo.get_state() == FileState.STATIC
        foo.detach()
        assert foo.is_detached()

        to_check = wfp.register_static_tree(plan, "data")
        assert to_check == {"data/foo.txt": fake_hash("data/foo.txt")}
        assert not foo.is_detached()
        assert foo.creator() == wfp.find(StaticTree, "data/")


async def test_declare_static_files_same_creator_twice(wfp: Workflow):
    """Re-declaring a static file with the same creator is a silent no-op."""
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        to_check1 = wfp.declare_static_files(plan, ["a.txt"])
        assert to_check1 == {"a.txt": FileHash.unknown()}
        to_check2 = wfp.declare_static_files(plan, ["a.txt"])
        assert to_check2 == {}
        assert [file.path for file in wfp.nodes(File) if file.path == "a.txt"] == ["a.txt"]
        assert wfp.find(File, "a.txt").creator() == plan


async def test_declare_static_files_other_creator_still_raises(wfp: Workflow):
    """Re-declaring a static file with a different creator is still an error."""
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "sub")
        sub = wfp.find(Step, "sub")
        wfp.declare_static_files(plan, ["a.txt"])
    with pytest.raises(GraphError):
        async with wfp.db:
            wfp.declare_static_files(sub, ["a.txt"])


async def test_declare_static_files_same_creator_after_confirm(wfp: Workflow):
    """The same-creator no-op also applies once the file has been confirmed STATIC."""
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        (a,) = declare_static(wfp, plan, ["a.txt"])
        assert a.get_state() == FileState.STATIC
        a_hash = a.get_hash()

        to_check = wfp.declare_static_files(plan, ["a.txt"])
        assert to_check == {}
        assert a.get_state() == FileState.STATIC
        assert a.get_hash() == a_hash


async def test_declare_static_files_same_creator_output_still_raises(wfp: Workflow):
    """Declaring a file the same step already produced as an output is still an error.

    An AWAITED file is not one of the three states a static declaration can produce, so
    it is a genuine contradiction, not a repeated declaration.
    """
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "touch a.txt", out_paths=["a.txt"])
        step = wfp.find(Step, "touch a.txt")
        assert wfp.find(File, "a.txt").get_state() == FileState.AWAITED
    with pytest.raises(GraphError):
        async with wfp.db:
            wfp.declare_static_files(step, ["a.txt"])


async def test_declare_static_files_detached_is_recycled(wfp: Workflow):
    """A detached same-creator file is recycled, not skipped as a no-op.

    This is the case `initialize_boot` relies on: a re-running step re-declares its own
    detached files and must get a fresh `to_check` entry so the hash is re-verified.
    """
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        (a,) = declare_static(wfp, plan, ["a.txt"])
        a.detach()
        assert a.is_detached()

        to_check = wfp.declare_static_files(plan, ["a.txt"])
        assert to_check == {"a.txt": fake_hash("a.txt")}
        assert not a.is_detached()
        assert a.creator() == plan


async def test_register_static_tree_same_creator_twice(wfp: Workflow):
    """Re-registering a static tree with the same creator is a silent no-op."""
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        to_check1 = wfp.register_static_tree(plan, "static")
        assert to_check1 == {}
        to_check2 = wfp.register_static_tree(plan, "static")
        assert to_check2 == {}
        assert list(wfp.nodes(StaticTree)) == [wfp.find(StaticTree, "static/")]


async def test_register_static_tree_same_creator_subdirectory(wfp: Workflow):
    """A subdirectory of the same creator's own tree is a no-op, not a rejection."""
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.register_static_tree(plan, "static")
        assert wfp.register_static_tree(plan, "static/sub") == {}
        assert wfp.find(StaticTree, "static/sub/") is None


async def test_register_static_tree_other_creator_subdirectory_raises(wfp: Workflow):
    """A subdirectory of another creator's tree is still rejected."""
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "sub")
        sub = wfp.find(Step, "sub")
        wfp.register_static_tree(plan, "static")
    with pytest.raises(GraphError):
        async with wfp.db:
            wfp.register_static_tree(sub, "static/sub")


async def test_register_static_tree_parent_still_raises(wfp: Workflow):
    """Registering the parent of an existing tree still raises, even for the same creator."""
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.register_static_tree(plan, "static/sub")
    with pytest.raises(GraphError):
        async with wfp.db:
            wfp.register_static_tree(plan, "static")


@pytest.mark.parametrize("sub_path", [STEPUP_DIR, f"{STEPUP_DIR}/", f"{STEPUP_DIR}/sub"])
async def test_register_static_tree_stepup_dir_raises(wfp: Workflow, sub_path: str):
    """A static tree can never be rooted at or under `.stepup`, like a static file."""
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        with pytest.raises(GraphError, match=re.escape(str(STEPUP_DIR))):
            wfp.register_static_tree(plan, sub_path)


@pytest.mark.parametrize("root_path", [".", "./", ""])
async def test_register_static_tree_root_raises(wfp: Workflow, root_path: str):
    """A static tree cannot be rooted at the project root.

    It would have to own `plan.py` and every step output.
    """
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        with pytest.raises(GraphError, match="project root"):
            wfp.register_static_tree(plan, root_path)


async def test_step_try_clean(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")

        # Simulate execution of plan to get a hash
        step_hash = StepHash(b"p" * 32, None, b"p" * 32, None)
        plan.mark_completed(step_hash, False)

        # Check presence of hash
        assert plan.get_hash() == step_hash

        # Run delete_detached() and verify that plan has been removed.
        plan.detach()
        wfp.delete_detached()
        assert not plan.in_graph()


async def test_step_lost_child(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "prog", out_paths=["data.txt"])
        step = wfp.find(Step, "prog")
        step.mark_completed(StepHash(b"prog", None, b"prog", None), False)
        step.detach()
        assert step.is_detached()

        # Simulate creation of new data.txt
        to_check = wfp.declare_static_files(wfp.root, ["data.txt"])
        assert to_check == {"data.txt": FileHash.unknown()}
        data = wfp.find(File, "data.txt")
        assert data.creator() == wfp.root

        # The step of prog is kept for a possible recycle, but it lost data.txt.
        # It must not be skipped anymore, and it can no longer be recycled as the step
        # that declares data.txt, since that output is not one of its declared outputs.
        assert step.in_graph()
        assert step.get_hash() is None
        assert list(step.out_paths(dynamic=False, include_detached=True)) == []
        assert not step.can_recycle(out_paths=["data.txt"])

        # The next cleanup removes it.
        wfp.delete_detached()
        assert not step.in_graph()
        assert list(wfp.nodes(Step, include_detached=True)) == [plan]


async def test_step_lost_dynamic_child(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "prog")
        step = wfp.find(Step, "prog")
        _amend(wfp, step, out_paths=["data.txt"])
        step.mark_completed(StepHash(b"prog", None, b"prog", None), False)
        assert step.get_hash() is not None
        step.detach()

        # Simulate creation of new data.txt
        declare_static(wfp, wfp.root, ["data.txt"])
        assert wfp.find(File, "data.txt").creator() == wfp.root

        # Dynamic outputs are not compared by can_recycle, so redeclaring the step recycles it.
        # It must run again to recreate data.txt, i.e. it must have lost its hash.
        assert step.get_hash() is None
        wfp.define_step(plan, "prog")
        again = wfp.find(Step, "prog")
        assert again.i == step.i
        assert not again.is_detached()
        assert again.get_hash() is None


async def test_step_lost_recycled_child(wfp: Workflow):
    """A detached step loses a created step to a new creator that recycles it."""
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "sub_plan")
        sub_plan = wfp.find(Step, "sub_plan")
        wfp.define_step(sub_plan, "work")
        work = wfp.find(Step, "work")
        sub_plan.mark_completed(StepHash(b"sub_plan", None, b"sub_plan", None), False)
        work.mark_completed(StepHash(b"work", None, b"work", None), False)
        sub_plan.detach()
        assert work.is_detached()

        # The top-level plan declares the same step itself, which recycles it as is.
        wfp.define_step(plan, "work")
        assert wfp.find(Step, "work").i == work.i
        assert not work.is_detached()
        assert work.get_hash() is not None

        # The sub plan lost work and must not be skipped when it is recycled later.
        assert sub_plan.in_graph()
        assert sub_plan.get_hash() is None


async def test_static_tree_lost_child(wfp: Workflow):
    async with wfp.db:
        # Construct a workflow with a statoc root
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "prog")
        prog = wfp.find(Step, "prog")
        wfp.register_static_tree(prog, "data")
        tree = wfp.find(StaticTree, "data/")

        # Simulate the creation of a static data/foo.txt through the static tree.
        to_check = wfp.define_step(prog, "work", inp_paths=["data/foo.txt"])
        assert to_check == {"data/foo.txt": FileHash.unknown()}
        wfp.update_file_hashes(
            {"data/foo.txt": fake_hash("data/foo.txt")}, cause=HashUpdateCause.CONFIRMED
        )

        prog.detach()
        assert prog.is_detached()

        # Redeclaring the same path must keep its previously confirmed hash (recycled node),
        # not lose it the way FileState.MISSING would.
        to_check = wfp.declare_static_files(wfp.root, ["data/foo.txt"])
        assert to_check == {"data/foo.txt": fake_hash("data/foo.txt")}
        wfp.update_file_hashes(
            {"data/foo.txt": fake_hash("data/foo.txt")}, cause=HashUpdateCause.CONFIRMED
        )
        data = wfp.find(File, "data/foo.txt")
        assert data.creator() == wfp.root

        # The static tree is kept until the next cleanup, but it is detached and must
        # therefore not claim any new file.
        assert tree.in_graph()
        assert tree.is_detached()
        wfp.define_step(plan, "other", inp_paths=["data/bar.txt"])
        assert wfp.find(File, "data/bar.txt").creator() is None

        # The next cleanup removes it.
        wfp.delete_detached()
        assert not tree.in_graph()
        assert list(wfp.nodes(StaticTree, include_detached=True)) == []


async def test_static_tree_lost_child_reregister(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "prog")
        prog = wfp.find(Step, "prog")
        wfp.register_static_tree(prog, "data")
        tree = wfp.find(StaticTree, "data/")
        wfp.define_step(prog, "work", inp_paths=["data/foo.txt"])
        wfp.update_file_hashes(
            {"data/foo.txt": fake_hash("data/foo.txt")}, cause=HashUpdateCause.CONFIRMED
        )

        # prog disappears from plan.py: the tree, and the file it created, become
        # detached leftovers (detachment propagates recursively to product nodes).
        prog.detach()
        assert tree.is_detached()
        foo = wfp.find(File, "data/foo.txt")
        assert foo.is_detached()

        # A new creator can still register the same static tree, reusing the node and
        # adopting the orphaned (detached) file that used to belong to it.
        wfp.define_step(plan, "prog2")
        prog2 = wfp.find(Step, "prog2")
        to_check = wfp.register_static_tree(prog2, "data")
        assert to_check == {"data/foo.txt": fake_hash("data/foo.txt")}
        new_tree = wfp.find(StaticTree, "data/")
        assert new_tree.i == tree.i
        assert new_tree.creator() == prog2
        assert not foo.is_detached()
        assert foo.creator() == new_tree


def _build_and_leave_behind(wfp: Workflow, target_state: FileState) -> tuple[Step, File]:
    """Build `data/foo.txt`, bring it to `target_state`, then detach its creator.

    Simulates a step that built a file and was subsequently dropped from `plan.py`,
    leaving its output behind as an untracked, detached file still carrying its
    build-time hash. `target_state` must be `FileState.BUILT` or `FileState.OUTDATED`.
    """
    plan = wfp.find(Step, "./plan.py")
    wfp.define_step(plan, "prog", out_paths=["data/foo.txt"])
    prog = wfp.find(Step, "prog")
    wfp.update_file_hashes(
        {"data/foo.txt": fake_hash("data/foo.txt")}, cause=HashUpdateCause.SUCCEEDED
    )
    foo = wfp.find(File, "data/foo.txt")
    assert foo.get_state() == FileState.BUILT
    if target_state == FileState.OUTDATED:
        # Demote BUILT -> OUTDATED (e.g. an input changed), still keeping the (now stale) hash.
        step = wfp.find(Step, "prog")
        step.set_state(StepState.SUCCEEDED)
        wfp.mark_step_pending(step)
        foo = wfp.find(File, "data/foo.txt")
    assert foo.get_state() == target_state
    assert not foo.get_hash().is_unknown

    # Simulate plan.py no longer declaring "prog": data/foo.txt is now a leftover.
    prog.detach()
    assert foo.is_detached()
    return prog, foo


@pytest.mark.parametrize("target_state", [FileState.BUILT, FileState.OUTDATED])
async def test_static_tree_adoption_clears_stale_build_hash(wfp: Workflow, target_state: FileState):
    """A BUILT/OUTDATED file left behind by a removed step must be re-hashed once adopted.

    Its stored hash is build-time provenance, not a confirmed source's content, so
    `register_static_tree`'s eager adoption sweep must not let it survive the recycle into
    UNCONFIRMED the way a STATIC-origin hash does (`test_static_tree_lost_child`).
    """
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        _, foo = _build_and_leave_behind(wfp, target_state)

        to_check = wfp.register_static_tree(plan, "data")
        assert to_check == {"data/foo.txt": FileHash.unknown()}
        assert foo.get_state() == FileState.UNCONFIRMED
        assert foo.get_hash().is_unknown


@pytest.mark.parametrize("target_state", [FileState.BUILT, FileState.OUTDATED])
async def test_supply_file_clears_stale_build_hash_on_lazy_adoption(
    wfp: Workflow, target_state: FileState
):
    """The lazy adoption path (`_resolve_supply_file`) must clear a stale BUILT/OUTDATED hash too.

    Fixing only `register_static_tree`'s eager sweep is not enough: a leftover build product
    that is only consumed later as a step input goes through this second call site, which must
    apply the same origin-state discrimination.
    """
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        _, foo = _build_and_leave_behind(wfp, target_state)

        # Declare the static tree directly, bypassing the eager adoption sweep, as a stand-in
        # for "the tree already exists and does not (yet) cover this leftover file."
        wfp.create(StaticTree, plan, "data/")
        assert foo.is_detached()

        to_check = wfp.define_step(plan, "other", inp_paths=["data/foo.txt"])
        assert to_check == {"data/foo.txt": FileHash.unknown()}
        assert foo.get_state() == FileState.UNCONFIRMED
        assert foo.get_hash().is_unknown


async def test_awaited_redeclare_unaffected_by_stale_build_hash_clear(wfp: Workflow):
    """Redeclaring an already-AWAITED file as AWAITED again must not be touched by the new clause.

    This pins that the `file_clear_hash` trigger's added `UNCONFIRMED`-with-`BUILT`/`OUTDATED`
    origin branch is scoped to `NEW.state = UNCONFIRMED` only, and does not affect the ordinary
    AWAITED re-declaration a step performs by redeclaring its own outputs before a rerun.
    """
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        out = wfp._declare_file(plan, "out.txt", FileState.AWAITED)
        assert out.get_state() == FileState.AWAITED
        assert out.get_hash().is_unknown

        # Simulate a restart: the file is detached, then redeclared AWAITED again before
        # ever having been built.
        out.detach()
        assert out.is_detached()
        out = wfp._declare_file(plan, "out.txt", FileState.AWAITED)
        assert out.get_state() == FileState.AWAITED
        assert out.get_hash().is_unknown


async def test_consistency_parent(wfp: Workflow):
    async with wfp.db:
        declare_static(wfp, wfp.find(Step, "./plan.py"), ["local.txt"])
        # Manually change local.txt to sub/local.txt
        wfp.db.execute("UPDATE node SET label = 'sub/local.txt' WHERE label = 'local.txt'")
        wfp._check_consistency()
        # Manually set it back, because wfp will get checked by fixture.
        wfp.db.execute("UPDATE node SET label = 'local.txt' WHERE label = 'sub/local.txt'")


async def test_consistency_succeeded_step(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "prog", out_paths=["out.txt"])
        step = wfp.find(Step, "prog")
        wfp.update_file_hashes({"out.txt": fake_hash("out.txt")}, cause=HashUpdateCause.SUCCEEDED)
        step.mark_completed(StepHash(b"prog", None, b"zzz", None), False)
        assert step.get_state() == StepState.SUCCEEDED
        out = wfp.find(File, "out.txt")
        assert out.get_state() == FileState.BUILT
        file_hashes = wfp.get_file_hashes(["out.txt"])
        assert file_hashes == {"out.txt": fake_hash("out.txt")}
    # Manually change the output file to AWAITED, which must clear the file hash.
    # However, this is still the output of a BUILT step, which should trip the consistency check.
    with pytest.raises(GraphError):  # noqa: PT012
        async with wfp.db:
            out.set_state(FileState.AWAITED)
            file_hashes = wfp.get_file_hashes(["out.txt"])
            wfp._check_consistency()
    assert file_hashes == {"out.txt": FileHash.unknown()}


async def test_sql_recurse_products_pending_tree(wfp: Workflow):
    async with wfp.db:
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


@pytest.mark.parametrize("inp_path", ["data/foo.txt", "data/sub/deep.txt", "data/sub/a/deep.txt"])
async def test_unconfirmed_inputs(wfp: Workflow, inp_path: str):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "prog", inp_paths=[inp_path])
        prog = wfp.find(Step, "prog")
        wfp.register_static_tree(plan, "data")
        rows = wfp.db.execute(UNCONFIRMED_INPUTS, (prog.i,)).fetchall()
        assert len(rows) == 1
        data = wfp.find(File, inp_path)
        assert File(wfp, *rows[0]) == data


async def test_recreate_step_to_check(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.register_static_tree(plan, "data")
        to_check = wfp.define_step(plan, "prog", inp_paths=["data/foo.txt"])
        assert to_check == {"data/foo.txt": FileHash.unknown()}
        prog = wfp.find(Step, "prog")
        prog.detach()
        to_check = wfp.define_step(plan, "prog", inp_paths=["data/foo.txt"])
        assert to_check == {"data/foo.txt": FileHash.unknown()}


async def test_recreate_step_to_check_amend(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.register_static_tree(plan, "static")
        to_check = wfp.define_step(
            plan,
            "prog",
            inp_paths=["static/inp1.txt"],
            out_paths=["out1.txt"],
            vol_paths=["vol1.txt"],
        )
        assert to_check == {"static/inp1.txt": FileHash.unknown()}
        wfp.update_file_hashes(
            {"static/inp1.txt": fake_hash("static/inp1.txt")}, cause=HashUpdateCause.CONFIRMED
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
        assert to_check == {}
        carry_on, to_check = _amend(
            wfp, prog, inp_paths=["other/inp2.txt"], out_paths=["out2.txt"], vol_paths=["vol2.txt"]
        )
        assert not carry_on
        assert to_check == {}


async def test_get_file_hashes(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        paths = ["data.txt", "other.txt"]
        wfp.declare_static_files(plan, paths)
        assert wfp.get_file_hashes(paths) == {
            "data.txt": FileHash.unknown(),
            "other.txt": FileHash.unknown(),
        }
        wfp.update_file_hashes({"data.txt": fake_hash("data.txt")}, cause=HashUpdateCause.CONFIRMED)
        assert wfp.get_file_hashes(paths) == {
            "data.txt": fake_hash("data.txt"),
            "other.txt": FileHash.unknown(),
        }


@pytest.mark.parametrize("wfs", [3], indirect=True)
async def test_defer_cap(wfs: Workflow):
    async with wfs.db:
        wfs.define_step(wfs.root, "echo")
        echo = wfs.find(Step, "echo")
        wfs.define_step(echo, "sub")
        sub = wfs.find(Step, "sub")

        # Deferred 3 times (== cap): stays PENDING each time, count increments,
        # and the opportunistically-created child stays attached (accepted defer).
        for expected_count in [1, 2, 3]:
            detached, interrupted_defer = echo.mark_completed(None, True)
            assert detached is False
            assert interrupted_defer is False
            assert echo.get_state() == StepState.PENDING
            assert echo.get_defer_count() == expected_count
            assert not sub.is_detached()

        # 4th defer (cap + 1): FAILED instead of PENDING, a genuine terminal
        # outcome, so the child is now detached too.
        detached, interrupted_defer = echo.mark_completed(None, True)
        assert detached is False
        assert interrupted_defer is True
        assert echo.get_state() == StepState.FAILED
        assert echo.get_defer_count() == 4
        assert sub.is_detached()


async def test_completed_detaches_child_only_on_genuine_failure(wfs: Workflow):
    """An accepted defer leaves opportunistically-created children attached;
    only a genuine terminal failure detaches them."""
    async with wfs.db:
        wfs.define_step(wfs.root, "echo")
        echo = wfs.find(Step, "echo")
        wfs.define_step(echo, "sub")
        sub = wfs.find(Step, "sub")
        assert not sub.is_detached()

        # Accepted defer: child must stay attached.
        detached, interrupted_defer = echo.mark_completed(None, True)
        assert not detached
        assert not interrupted_defer
        assert echo.get_state() == StepState.PENDING
        assert not sub.is_detached()

        # Genuine terminal failure (no defer requested): child is detached.
        echo.set_state(StepState.RUNNING)
        detached, interrupted_defer = echo.mark_completed(None, False)
        assert not detached
        assert not interrupted_defer
        assert echo.get_state() == StepState.FAILED
        assert sub.is_detached()


@pytest.mark.parametrize("wfs", [3], indirect=True)
async def test_defer_count_reset_on_success(wfs: Workflow):
    async with wfs.db:
        wfs.define_step(wfs.root, "echo")
        echo = wfs.find(Step, "echo")

        echo.mark_completed(None, True)
        assert echo.get_defer_count() == 1

        # A genuine success resets the counter to 0.
        step_hash = StepHash(b"h" * 32, None, b"h" * 32, None)
        echo.mark_completed(step_hash, False)
        assert echo.get_state() == StepState.SUCCEEDED
        assert echo.get_defer_count() == 0

        # Next defer cycle starts back at 1, not 2.
        echo.set_state(StepState.PENDING)
        echo.mark_completed(None, True)
        assert echo.get_defer_count() == 1


async def test_defer_clear_independent_of_count(wfs: Workflow):
    # Uses the default cap (100): a single genuine (non-defer) FAILED step
    # still leaves defer_count untouched (only SUCCEEDED resets it).
    async with wfs.db:
        wfs.define_step(wfs.root, "echo")
        echo = wfs.find(Step, "echo")
        echo.mark_completed(None, True)
        assert echo.get_defer_count() == 1
        # A genuine, unrelated command failure on the next run (no defer requested).
        echo.set_state(StepState.PENDING)
        _detached, interrupted_defer = echo.mark_completed(None, False)
        assert interrupted_defer is False  # plain FAILED, not a cap-exceeded defer
        assert echo.get_state() == StepState.FAILED
        assert echo.get_defer_count() == 1  # unchanged by a plain FAILED


async def test_completed_reclaims_unavailable_input_available_during_run(wfs: Workflow):
    """`completed()` re-derives dynamic-input availability directly from the graph,
    instead of trusting a stale amend-time snapshot.

    This matters because a producer may complete (and call `mark_step_pending()`) while
    the consuming step is still RUNNING: `mark_step_pending()` is then a no-op (see
    `Workflow.mark_step_pending()`), and the file will never transition state again
    through that path. `completed()` must still notice the input is available by
    re-querying the graph, rather than trusting the caller's stale `wants_defer`
    judgment.
    """
    async with wfs.db:
        wfs.define_step(wfs.root, "plan")
        plan = wfs.find(Step, "plan")
        wfs.define_step(plan, "producer", out_paths=["data.txt"])
        producer = wfs.find(Step, "producer")
        wfs.define_step(plan, "sink")
        sink = wfs.find(Step, "sink")

        sink.set_state(StepState.RUNNING)
        is_detached, unavailable, unfresh, _ = amend_step(wfs, sink, inp_paths=["data.txt"])
        assert not is_detached
        assert unavailable == {"data.txt"}
        assert not unfresh

        # `producer` completes while `sink` is still RUNNING: mark_pending() on a
        # RUNNING step is a no-op, so nothing clears the (would-be) memo through that path.
        out_hashes = {"data.txt": fake_hash("data.txt")}
        wfs.update_file_hashes(out_hashes, cause=HashUpdateCause.SUCCEEDED)
        step_hash = StepHash.from_inp(producer.key(), True, {}, {})
        step_hash = step_hash.evolve_out(out_hashes)
        producer.mark_completed(step_hash, False)

        # completed() re-derives availability from the graph, so even though wants_defer
        # is stale True, the deferred flag reflects the input's current availability.
        sink.mark_completed(None, True)
        assert sink.get_state() == StepState.PENDING
        deferred = wfs.db.execute("SELECT deferred FROM step WHERE node = ?", (sink.i,)).fetchone()[
            0
        ]
        assert not deferred


async def test_clean_stepup_root_parents(wfs: Workflow):
    async with wfs.db:
        declare_static(wfs, wfs.root, ["../foo.txt"])
        assert wfs.find(File, "../foo.txt").get_state() == FileState.STATIC
        wfs.delete_detached()
        assert wfs.find(File, "../foo.txt").get_state() == FileState.STATIC


async def test_large_inode(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        large_inode = 0x8000000000000001
        wfp.declare_static_files(plan, ["foo.txt"])
        wfp.update_file_hashes(
            {"foo.txt": FileHash(hashlib.sha256(b"foo").digest(), 0o644, 1.0, 10, large_inode)},
            cause=HashUpdateCause.CONFIRMED,
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
async def test_define_step_invalid_resources(wfp: Workflow, resources: dict):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
    with pytest.raises(sqlite3.IntegrityError):
        async with wfp.db:
            wfp.define_step(plan, "echo", resources=resources)


async def test_step_outcome_roundtrip(wfp: Workflow):
    """set_outcome / get_outcome / delete_outcome round-trip a `ChildOutcome`."""
    async with wfp.db:
        step = wfp.find(Step, "./plan.py")

        # No outcome stored yet.
        assert step.get_outcome() is None

        # Empty content round-trips like any other outcome.
        step.set_outcome(ChildOutcome(0, "", ""), 0)
        assert step.get_outcome() == ChildOutcome(0, "", "")

        # Non-empty content for both streams round-trips in one call.
        outcome = ChildOutcome(1, "hello out\n", "hello err\n")
        step.set_outcome(outcome, 0)
        assert step.get_outcome() == outcome

        # delete_outcome clears the stored outcome.
        step.delete_outcome()
        assert step.get_outcome() is None


async def test_step_outcome_truncated_on_store(wfp: Workflow):
    """set_outcome applies the byte budget independently per stream."""
    async with wfp.db:
        step = wfp.find(Step, "./plan.py")
        step.set_outcome(ChildOutcome(0, "abcdefghij", "klmnopqrst"), 5)
        assert step.get_outcome() == ChildOutcome(
            0,
            "abcde\n[output truncated at 5 bytes]\n",
            "klmno\n[output truncated at 5 bytes]\n",
        )


async def test_step_outcome_clean_no_fk_error(wfp: Workflow):
    """delete_detached() removes the step row (and implicitly its outcome columns)."""
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "echo hi")
        step = wfp.find(Step, "echo hi")
        step.set_outcome(ChildOutcome(0, "data\n", "oops\n"), 0)
        step_i = step.i
        step.detach()
        wfp.delete_detached()
        # After delete_detached() deletes the node row, the step no longer exists in the database.
        assert wfp.db.execute("SELECT 1 FROM step WHERE node = ?", (step_i,)).fetchone() is None


async def test_step_subprocess_roundtrip(wfp: Workflow):
    """record_subprocess stores all fields; direct query round-trips the public ones."""
    async with wfp.db:
        step = wfp.find(Step, "./plan.py")

        # No records yet.
        query = "SELECT * FROM step_subprocess WHERE node = ? ORDER BY rowid"
        rows = wfp.db.execute(query, (step.i,)).fetchall()
        assert rows == []

        # Record two invocations: one with an env overlay, a non-zero return code, and shell=False;
        # one with shell=True, a stdin string, and captured stdout/stderr.
        step.add_subprocess(
            "typst compile a.typ a.pdf",
            "sub",
            {"TR": "/x"},
            7,
            False,
            "input\n",
            "output\n",
            "error\n",
        )
        step.add_subprocess(
            "echo hi | tr a b", ".", None, 0, True, "feed me\n", "hi\n", "warning\n"
        )

        # Query yields (cmd, workdir, env_overrides, returncode, shell, stdin, stdout, stderr)
        # in insertion order, with cmd stored verbatim and env decoded back to a dict.
        rows = wfp.db.execute(query, (step.i,)).fetchall()
        assert rows == [
            (
                3,
                "typst compile a.typ a.pdf",
                "sub",
                '{"TR": "/x"}',
                7,
                0,
                "input\n",
                "output\n",
                "error\n",
            ),
            (3, "echo hi | tr a b", ".", None, 0, 1, "feed me\n", "hi\n", "warning\n"),
        ]


async def test_step_subprocess_clean_then_reinsert(wfp: Workflow):
    """delete_subprocesses removes all rows and a later record_subprocess call still works."""
    async with wfp.db:
        step = wfp.find(Step, "./plan.py")
        step.add_subprocess("a", ".", None, 0, False, "in1", "out1", "err1")
        step.add_subprocess("b", ".", None, 0, True, "in2", "out2", "err2")
        # Query yields the rows in insertion order (cmd is field 0).
        query = "SELECT * FROM step_subprocess WHERE node = ? ORDER BY rowid"
        rows = wfp.db.execute(query, (step.i,)).fetchall()
        assert rows == [
            (3, "a", ".", None, 0, 0, "in1", "out1", "err1"),
            (3, "b", ".", None, 0, 1, "in2", "out2", "err2"),
        ]

        step.delete_subprocesses()
        rows = wfp.db.execute(query, (step.i,)).fetchall()
        assert rows == []

        # A fresh record after cleanup is the only row left.
        step.add_subprocess("c", ".", None, 0, False, "in3", "out3", "err3")
        rows = wfp.db.execute(query, (step.i,)).fetchall()
        assert rows == [
            (3, "c", ".", None, 0, 0, "in3", "out3", "err3"),
        ]


async def test_step_subprocess_reset_for_rerun(wfp: Workflow):
    """reset_for_rerun drops subprocess rows recorded by a previous run."""
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "echo hi")
        step = wfp.find(Step, "echo hi")
        step.add_subprocess("echo hi", ".", None, 0, False, "in", "out", "err")
        query = "SELECT * FROM step_subprocess WHERE node = ? ORDER BY rowid"
        rows = wfp.db.execute(query, (step.i,)).fetchall()
        assert len(rows) == 1
        step.reset_for_rerun()
        rows = wfp.db.execute(query, (step.i,)).fetchall()
        assert rows == []


async def test_step_subprocess_clean_no_fk_error(wfp: Workflow):
    """delete_detached() removes recorded subprocesses of a deleted step, without an FK error."""
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "echo hi")
        step = wfp.find(Step, "echo hi")
        step.add_subprocess("echo hi", ".", None, 0, False, "in", "out", "err")
        # delete_detached() deletes the node row,
        # and the step_subprocess rows are removed automatically by the ON DELETE CASCADE
        # foreign key.
        step.detach()
        wfp.delete_detached()
        query = "SELECT * FROM step_subprocess WHERE node = ? ORDER BY rowid"
        rows = wfp.db.execute(query, (step.i,)).fetchall()
        assert rows == []


# Satellite tables whose rows hang off a node and are removed by ON DELETE CASCADE.
SATELLITE_NODE_TABLES = (
    "step",
    "env_var",
    "step_resource",
    "step_subprocess",
    "nglob",
)


async def test_clean_cascades_satellite_rows(wfs: Workflow):
    """Cleaning a node deletes all its satellite rows via ON DELETE CASCADE.

    No explicit per-table DELETE is issued in `Step.before_delete()` and `File.before_delete()`.
    The cascade fires when `Trellis.delete_detached()` deletes the node row.
    """
    async with wfs.db:
        # Foreign-key enforcement must be active on the connection or the cascades never fire.
        assert wfs.db.execute("PRAGMA foreign_keys").fetchone()[0] == 1

        # Build a step that owns a row in every satellite table, plus an output file.
        declare_static(wfs, wfs.root, ["inp.txt"])
        wfs.define_step(
            wfs.root,
            "do something",
            inp_paths=["inp.txt"],
            env_deps=["SOME_VAR"],
            out_paths=["out.txt"],
            resources={"cpu": 2},
        )
        step = wfs.find(Step, "do something")
        out_file = wfs.find(File, "out.txt")
        step.set_hash(StepHash(b"inp", None, b"out", None))
        step.set_outcome(ChildOutcome(0, "hello\n", ""), 0)
        step.add_subprocess("do something", ".", None, 0, False, "in", "out", "err")
        wfs.register_nglob(step, NamedGlob("*.txt"))
        step_i = step.i
        out_i = out_file.i

        # Sanity check: a row exists in each satellite table and the output file table.
        for table in SATELLITE_NODE_TABLES:
            count = wfs.db.execute(f"SELECT count(*) FROM {table} WHERE node = ?", (step_i,))
            assert count.fetchone()[0] >= 1, f"expected a row in {table}"
        assert (
            wfs.db.execute("SELECT count(*) FROM file WHERE node = ?", (out_i,)).fetchone()[0] == 1
        )

        # Detach and clean: the step and its output file node are removed, cascading their rows.
        step.detach()
        wfs.delete_detached()

        assert wfs.db.execute("SELECT count(*) FROM node WHERE i = ?", (step_i,)).fetchone()[0] == 0
        assert wfs.db.execute("SELECT count(*) FROM node WHERE i = ?", (out_i,)).fetchone()[0] == 0
        for table in SATELLITE_NODE_TABLES:
            count = wfs.db.execute(f"SELECT count(*) FROM {table} WHERE node = ?", (step_i,))
            assert count.fetchone()[0] == 0, f"orphan row left in {table}"
        assert (
            wfs.db.execute("SELECT count(*) FROM file WHERE node = ?", (out_i,)).fetchone()[0] == 0
        )


#
# Build targets (Stage 1: plumbing and declaration-time validation only)
#


@pytest_asyncio.fixture
async def wfs_target(request) -> AsyncIterator[Workflow]:
    """A from-scratch workflow constructed with a custom `targets` set.

    Indirect parametrization supplies the `targets` collection, e.g.
    `@pytest.mark.parametrize("wfs_target", [["out.txt"]], indirect=True)`.
    """
    dir_queue = asyncio.Queue()
    with DBSession.open(":memory:") as db:
        workflow = Workflow(
            db, create_parent_dirs=False, dir_queue=dir_queue, targets=request.param
        )
        await workflow.initialize()
        yield workflow
        async with db:
            workflow._check_consistency()


def _init_target_dir(workflow, *paths):
    """Create and populate the `target_dir` temp table, mirroring `Scheduler.initialize()`.

    `reconcile_targets()`'s bulk range `UPDATE` joins this table; tests that call
    `reconcile_targets()` directly (without a full `Scheduler`) must set it up themselves.
    Must be called inside `async with workflow.db:`.
    """
    workflow.db.execute(
        "CREATE TEMPORARY TABLE IF NOT EXISTS target_dir "
        "(path TEXT PRIMARY KEY, upper TEXT NOT NULL)"
    )
    workflow.db.execute("DELETE FROM target_dir")
    workflow.db.executemany(
        "INSERT INTO target_dir VALUES (?, ?)",
        ((path, dir_range_upper(path)) for path in paths),
    )


async def test_need_threshold_property_no_targets(wfs: Workflow):
    assert wfs.need_threshold == Need.OPTIONAL


@pytest.mark.parametrize("wfs_target", [["out.txt"]], indirect=True)
async def test_need_threshold_property_with_targets(wfs_target: Workflow):
    assert wfs_target.need_threshold == Need.DEFAULT


async def test_need_threshold_property_with_target_dirs_only():
    """A build given only directory targets must also flip the threshold to DEFAULT.

    This guards the `or self.target_dirs` half of `need_threshold`: without it, a
    dirs-only build would dispatch every DEFAULT step in the project while appearing
    to work (the targeted subtree does build too).
    """
    dir_queue = asyncio.Queue()
    with DBSession.open(":memory:") as db:
        workflow = Workflow(db, create_parent_dirs=False, dir_queue=dir_queue, target_dirs=["out/"])
        await workflow.initialize()
        assert workflow.need_threshold == Need.DEFAULT


async def test_define_step_rejects_need_target(wfp: Workflow):
    # need=Need.TARGET is rejected by the step table's need CHECK constraint (see
    # STEP_SCHEMA); Workflow.define_step() no longer duplicates this check in Python.
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
    with pytest.raises(sqlite3.IntegrityError):
        async with wfp.db:
            wfp.define_step(plan, "echo", need=Need.TARGET)


@pytest.mark.parametrize("wfs_target", [["out.txt"]], indirect=True)
async def test_define_step_target_volatile_output(wfs_target: Workflow):
    with pytest.raises(GraphError):
        async with wfs_target.db:
            wfs_target.define_step(wfs_target.root, "touch out.txt", vol_paths=["out.txt"])


@pytest.mark.parametrize("wfs_target", [["out.txt"]], indirect=True)
async def test_define_step_target_static_output(wfs_target: Workflow):
    with pytest.raises(GraphError):
        async with wfs_target.db:
            wfs_target.declare_static_files(wfs_target.root, ["out.txt"])


@pytest.mark.parametrize("wfs_target", [["out.txt"]], indirect=True)
async def test_amend_step_target_volatile_output(wfs_target: Workflow):
    async with wfs_target.db:
        wfs_target.define_step(wfs_target.root, "echo")
        echo = wfs_target.find(Step, "echo")
    with pytest.raises(GraphError):
        async with wfs_target.db:
            _amend(wfs_target, echo, vol_paths=["out.txt"])


async def test_define_step_recycle_target_volatile_output():
    """A target's volatile-output rejection must also fire on `define_step`'s recycle path.

    `Step.reattach()` reattaches a detached VOLATILE product row without going through
    `_declare_file`, so this needs its own guard (checked directly on `vol_paths`,
    before `Trellis.try_recycle()` is attempted). To exercise it, the step is first declared
    and detached on a *targetless* workflow (simulating a previous director process),
    then redeclared identically on a second `Workflow` instance sharing the same
    database, this time constructed with a matching target.
    """
    dir_queue = asyncio.Queue()
    with DBSession.open(":memory:") as db:
        workflow1 = Workflow(db, create_parent_dirs=False, dir_queue=dir_queue)
        await workflow1.initialize()
        async with db:
            workflow1.define_step(workflow1.root, "touch out.txt", vol_paths=["out.txt"])
            workflow1.find(Step, "touch out.txt").detach()

        workflow2 = Workflow(db, create_parent_dirs=False, dir_queue=dir_queue, targets=["out.txt"])
        await workflow2.initialize()
        with pytest.raises(GraphError):
            async with db:
                workflow2.define_step(workflow2.root, "touch out.txt", vol_paths=["out.txt"])


@pytest.mark.parametrize("wfs_target", [["static/foo/bar.txt"]], indirect=True)
async def test_resolve_supply_file_target_static_tree(wfs_target: Workflow):
    """A target resolving to a static-tree match must be rejected as a supplied input.

    This is a genuinely separate code path from `_declare_file`: `_resolve_supply_file`
    creates the MISSING file node itself, directly, without ever calling `_declare_file`.
    """
    async with wfs_target.db:
        wfs_target.define_step(wfs_target.root, "./plan.py")
        plan = wfs_target.find(Step, "./plan.py")
        wfs_target.register_static_tree(plan, "static")
    with pytest.raises(GraphError):
        async with wfs_target.db:
            wfs_target.define_step(plan, "cat static/foo/bar.txt", inp_paths=["static/foo/bar.txt"])


async def test_resolve_supply_file_target_static_existing():
    """A target matching a pre-existing STATIC file must be rejected when supplied as an input.

    The STATIC row is confirmed on a *targetless* workflow first (simulating a previous
    director process), since a targeted workflow would already reject the declaration
    itself via `_declare_file`. A second `Workflow` instance, sharing the database and
    constructed with a matching target, then supplies it as an input.
    """
    dir_queue = asyncio.Queue()
    with DBSession.open(":memory:") as db:
        workflow1 = Workflow(db, create_parent_dirs=False, dir_queue=dir_queue)
        await workflow1.initialize()
        async with db:
            declare_static(workflow1, workflow1.root, ["input.txt"])

        workflow2 = Workflow(
            db, create_parent_dirs=False, dir_queue=dir_queue, targets=["input.txt"]
        )
        await workflow2.initialize()
        with pytest.raises(GraphError):
            async with db:
                workflow2.define_step(workflow2.root, "cat input.txt", inp_paths=["input.txt"])


async def test_need_column_check_rejects_target(wfp: Workflow):
    """The tightened `need` CHECK constraint rejects a direct write of TARGET (33)."""
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "echo")
        step = wfp.find(Step, "echo")
    with pytest.raises(sqlite3.IntegrityError):
        async with wfp.db:
            wfp.db.execute("UPDATE step SET need = 33 WHERE node = ?", (step.i,))


async def test_node_creator_kind_check_rejects_file_creator_for_file(wfp: Workflow):
    """A `file` node's creator must be a step, static tree or root, not another file.

    This used to be rejected by `Workflow._check_creator` in Python; it is now only
    caught by the `node_check_creator_kind_ins` trigger (WORKFLOW_SCHEMA, workflow.py).
    """
    async with wfp.db:
        file_plan = wfp.find(File, "plan.py")
    with pytest.raises(sqlite3.IntegrityError):
        async with wfp.db:
            wfp.db.execute(
                "INSERT INTO node (kind, label, creator, detached) "
                "VALUES ('file', 'bad.txt', ?, FALSE)",
                (file_plan.i,),
            )


async def test_node_creator_kind_check_rejects_root_creator_for_static_tree(wfp: Workflow):
    """A static tree's creator must be a step, not root directly."""
    with pytest.raises(sqlite3.IntegrityError):
        async with wfp.db:
            wfp.db.execute(
                "INSERT INTO node (kind, label, creator, detached) VALUES ('st', 'sub/', ?, FALSE)",
                (wfp.root.i,),
            )


async def test_node_creator_kind_check_rejects_on_update(wfp: Workflow):
    """The `_upd` variant of the trigger fires on a raw creator `UPDATE`, e.g. a detached
    node reattached by code that bypasses `Node.reattach()`."""
    async with wfp.db:
        file_plan = wfp.find(File, "plan.py")
        detached_file = wfp.create(File, None, "detached.txt", state=FileState.MISSING)
        node_i = detached_file.i
    with pytest.raises(sqlite3.IntegrityError):
        async with wfp.db:
            wfp.db.execute("UPDATE node SET creator = ? WHERE i = ?", (file_plan.i, node_i))


async def test_dependency_kind_check_rejects_file_to_file(wfp: Workflow):
    """A file -> file dependency edge is not one of the three allowed kind combinations.

    This used to be rejected by `Workflow._check_source` in Python; it is now only caught
    by the `dependency_check_kinds_ins` trigger (WORKFLOW_SCHEMA, workflow.py).
    """
    async with wfp.db:
        file_a = wfp.create(File, None, "a.txt", state=FileState.MISSING)
        file_b = wfp.create(File, None, "b.txt", state=FileState.MISSING)
    with pytest.raises(sqlite3.IntegrityError):
        async with wfp.db:
            wfp.db.execute(
                "INSERT INTO dependency (source, sink) VALUES (?, ?)", (file_a.i, file_b.i)
            )


async def test_dependency_kind_check_rejects_step_to_step(wfp: Workflow):
    """A step -> step dependency edge is not one of the three allowed kind combinations."""
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "echo a")
        wfp.define_step(plan, "echo b")
        step_a = wfp.find(Step, "echo a")
        step_b = wfp.find(Step, "echo b")
    with pytest.raises(sqlite3.IntegrityError):
        async with wfp.db:
            wfp.db.execute(
                "INSERT INTO dependency (source, sink) VALUES (?, ?)", (step_a.i, step_b.i)
            )


#
# Build targets (Stage 2: reconcile_targets, startup reconciliation for resumed runs)
#


async def test_reconcile_targets_raises_when_no_pending_creator():
    """A stale VOLATILE target row raises when nothing can re-declare it.

    The VOLATILE row is created on a *targetless* workflow first (simulating a previous
    director process), then a second `Workflow` sharing the database is constructed with
    a matching target -- mirroring `test_define_step_recycle_target_volatile_output`. The
    declaring step is advanced past PENDING, so `_creator_chain_pending()` finds nothing
    that could re-declare the file differently, and `reconcile_targets()` must raise.
    """
    dir_queue = asyncio.Queue()
    with DBSession.open(":memory:") as db:
        workflow1 = Workflow(db, create_parent_dirs=False, dir_queue=dir_queue)
        await workflow1.initialize()
        async with db:
            workflow1.define_step(workflow1.root, "touch out.txt", vol_paths=["out.txt"])
            workflow1.find(Step, "touch out.txt").set_state(StepState.SUCCEEDED)

        workflow2 = Workflow(db, create_parent_dirs=False, dir_queue=dir_queue, targets=["out.txt"])
        await workflow2.initialize()
        async with db:
            _init_target_dir(workflow2)
            with pytest.raises(GraphError):
                workflow2.reconcile_targets()


async def test_reconcile_targets_skips_when_creator_pending():
    """The same stale VOLATILE target row is silently skipped while its creator is PENDING.

    A PENDING creator may re-declare the file differently when it reruns, so raising here
    would block a legitimate build (revision-6 guard in the design doc).
    """
    dir_queue = asyncio.Queue()
    with DBSession.open(":memory:") as db:
        workflow1 = Workflow(db, create_parent_dirs=False, dir_queue=dir_queue)
        await workflow1.initialize()
        async with db:
            workflow1.define_step(workflow1.root, "touch out.txt", vol_paths=["out.txt"])
            # A freshly declared step defaults to PENDING (Step.initialize()).

        workflow2 = Workflow(db, create_parent_dirs=False, dir_queue=dir_queue, targets=["out.txt"])
        await workflow2.initialize()
        async with db:
            _init_target_dir(workflow2)
            workflow2.reconcile_targets()  # must not raise


@pytest.mark.parametrize("wfs_target", [["missing.txt"]], indirect=True)
async def test_reconcile_targets_skips_target_with_no_file_row(wfs_target: Workflow):
    """A target with no matching `File` row at all is silently skipped."""
    async with wfs_target.db:
        _init_target_dir(wfs_target)
        wfs_target.reconcile_targets()  # must not raise


@pytest.mark.parametrize("wfs_target", [["out.txt"]], indirect=True)
async def test_reconcile_targets_skips_detached_file(wfs_target: Workflow):
    """A target matching a detached `File` row is silently skipped.

    Detached rows may be garbage from an abandoned plan; raising on those would block
    legitimate builds. Declaration-time checks and the not-produced warning cover these.
    """
    async with wfs_target.db:
        wfs_target.define_step(wfs_target.root, "touch out.txt", out_paths=["out.txt"])
        wfs_target.find(Step, "touch out.txt").detach()
    async with wfs_target.db:
        _init_target_dir(wfs_target)
        wfs_target.reconcile_targets()  # must not raise


@pytest.mark.parametrize("wfs_target", [["out.txt"]], indirect=True)
async def test_reconcile_targets_flags_producer_check_after(wfs_target: Workflow):
    """A target matching an active `File` row flags its creator step's `_check_after`."""
    async with wfs_target.db:
        wfs_target.define_step(wfs_target.root, "touch out.txt", out_paths=["out.txt"])
        step = wfs_target.find(Step, "touch out.txt")
        # Clear the flag define_step already set, to isolate reconcile_targets()'s own effect.
        wfs_target.db.execute("UPDATE step SET _check_after = 0 WHERE node = ?", (step.i,))
    async with wfs_target.db:
        _init_target_dir(wfs_target)
        wfs_target.reconcile_targets()
    async with wfs_target.db:
        row = wfs_target.db.execute(
            "SELECT _check_after FROM step WHERE node = ?", (step.i,)
        ).fetchone()
    assert row[0] == 1


async def test_reconcile_targets_flags_stale_target_implied_need(wfs: Workflow):
    """A step with a stale `_implied_need = TARGET` is flagged, regardless of current targets.

    `wfs` has no targets at all, matching the design doc's note that this UPDATE runs "on
    every director start, targeted or not" -- it is what demotes a chain left elevated by a
    previous run with a different target set.
    """
    async with wfs.db:
        wfs.define_step(wfs.root, "echo")
        step = wfs.find(Step, "echo")
        wfs.db.execute(
            "UPDATE step SET _implied_need = ?, _check_after = 0 WHERE node = ?",
            (Need.TARGET.value, step.i),
        )
    async with wfs.db:
        _init_target_dir(wfs)
        wfs.reconcile_targets()
    async with wfs.db:
        row = wfs.db.execute("SELECT _check_after FROM step WHERE node = ?", (step.i,)).fetchone()
    assert row[0] == 1


async def test_reconcile_targets_dir_flags_producer_check_after():
    """A new directory target flags the `_check_after` bit of a previously out-of-scope producer.

    Mirrors `test_reconcile_targets_flags_producer_check_after` but for a directory target
    on a reopened database: `workflow1` builds a graph with no targets at all; `workflow2`,
    sharing the database and constructed with `target_dirs=["out/"]`, must flag the
    producer's `_check_after` even though the file was never declared as an exact target.
    """
    dir_queue = asyncio.Queue()
    with DBSession.open(":memory:") as db:
        workflow1 = Workflow(db, create_parent_dirs=False, dir_queue=dir_queue)
        await workflow1.initialize()
        async with db:
            workflow1.define_step(
                workflow1.root, "touch out/report.txt", out_paths=["out/report.txt"]
            )
            step = workflow1.find(Step, "touch out/report.txt")
            workflow1.db.execute("UPDATE step SET _check_after = 0 WHERE node = ?", (step.i,))

        workflow2 = Workflow(
            db, create_parent_dirs=False, dir_queue=dir_queue, target_dirs=["out/"]
        )
        await workflow2.initialize()
        async with db:
            _init_target_dir(workflow2, "out/")
            workflow2.reconcile_targets()
        async with db:
            row = workflow2.db.execute(
                "SELECT _check_after FROM step WHERE node = ?", (step.i,)
            ).fetchone()
        assert row[0] == 1


async def test_reconcile_targets_dir_removed_flags_stale_implied_need():
    """A step left elevated by a removed directory target is flagged via the stale-need reset.

    De-elevation for directory targets reuses the existing `_implied_need = TARGET` reset
    (settled in the design doc): no directory-target-specific code path is needed for the
    stale direction, only for newly-matching targets.
    """
    dir_queue = asyncio.Queue()
    with DBSession.open(":memory:") as db:
        workflow1 = Workflow(
            db, create_parent_dirs=False, dir_queue=dir_queue, target_dirs=["out/"]
        )
        await workflow1.initialize()
        async with db:
            workflow1.define_step(
                workflow1.root, "touch out/report.txt", out_paths=["out/report.txt"]
            )
            step = workflow1.find(Step, "touch out/report.txt")
            workflow1.db.execute(
                "UPDATE step SET _implied_need = ?, _check_after = 0 WHERE node = ?",
                (Need.TARGET.value, step.i),
            )

        # workflow2 shares the database but is constructed without target_dirs, simulating
        # the directory target being removed on the next director start.
        workflow2 = Workflow(db, create_parent_dirs=False, dir_queue=dir_queue)
        await workflow2.initialize()
        async with db:
            _init_target_dir(workflow2)
            workflow2.reconcile_targets()
        async with db:
            row = workflow2.db.execute(
                "SELECT _check_after FROM step WHERE node = ?", (step.i,)
            ).fetchone()
        assert row[0] == 1


async def test_creator_chain_pending_detects_pending_ancestor(wfp: Workflow):
    """A PENDING step further up the creator chain (not the immediate creator) is detected."""
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")  # still PENDING (wfp's default)
        wfp.define_step(plan, "echo", out_paths=["out.txt"])
        # FAILED (not SUCCEEDED): _check_consistency() would flag a SUCCEEDED step with a
        # non-BUILT output at fixture teardown, which is beside the point of this test.
        wfp.find(Step, "echo").set_state(StepState.FAILED)
        out_file = wfp.find(File, "out.txt")
    async with wfp.db:
        assert wfp._creator_chain_pending(out_file) is True


async def test_creator_chain_pending_false_when_nothing_pending(wfp: Workflow):
    """The creator chain walk returns False (and terminates at Root) when nothing is PENDING."""
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        plan.set_state(StepState.FAILED)
        wfp.define_step(plan, "echo", out_paths=["out.txt"])
        wfp.find(Step, "echo").set_state(StepState.FAILED)
        out_file = wfp.find(File, "out.txt")
    async with wfp.db:
        assert wfp._creator_chain_pending(out_file) is False


#
# Build targets (Stage 3: is_regular_output)
#


async def test_is_regular_output_true_for_awaited(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "prog", out_paths=["out.txt"])
    async with wfp.db:
        assert wfp.is_regular_output("out.txt") is True


async def test_is_regular_output_true_for_built(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "prog", out_paths=["out.txt"])
        wfp.update_file_hashes({"out.txt": fake_hash("out.txt")}, cause=HashUpdateCause.SUCCEEDED)
    async with wfp.db:
        assert wfp.find(File, "out.txt").get_state() == FileState.BUILT
        assert wfp.is_regular_output("out.txt") is True


async def test_is_regular_output_true_for_outdated(wfp: Workflow):
    """An OUTDATED output (rebuild pending, e.g. after an input changed) is still regular."""
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "prog", out_paths=["out.txt"])
        wfp.update_file_hashes({"out.txt": fake_hash("out.txt")}, cause=HashUpdateCause.SUCCEEDED)
        step = wfp.find(Step, "prog")
        step.set_state(StepState.SUCCEEDED)
        wfp.mark_step_pending(step)  # Demotes the BUILT output to OUTDATED.
    async with wfp.db:
        assert wfp.find(File, "out.txt").get_state() == FileState.OUTDATED
        assert wfp.is_regular_output("out.txt") is True


async def test_is_regular_output_false_for_static(wfp: Workflow):
    """A STATIC file declared by a step (e.g. via `static()`) is not one of its outputs."""
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        declare_static(wfp, plan, ["static.txt"])
    async with wfp.db:
        assert wfp.is_regular_output("static.txt") is False


async def test_is_regular_output_false_for_volatile(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "touch vol.txt", vol_paths=["vol.txt"])
    async with wfp.db:
        assert wfp.is_regular_output("vol.txt") is False


async def test_is_regular_output_false_for_detached(wfp: Workflow):
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "prog", out_paths=["out.txt"])
        wfp.find(Step, "prog").detach()
    async with wfp.db:
        assert wfp.is_regular_output("out.txt") is False


async def test_is_regular_output_false_for_input_only(wfp: Workflow):
    """A file only ever supplied as an input has no `Step` creator (`_resolve_supply_file`
    creates it with `creator=None`), so it is not "produced" even though it exists."""
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "cat inp", inp_paths=["inp"])
    async with wfp.db:
        assert wfp.is_regular_output("inp") is False


async def test_is_regular_output_false_for_no_file_row(wfp: Workflow):
    async with wfp.db:
        assert wfp.is_regular_output("nope.txt") is False


#
# Build targets (Stage 5: has_regular_output_under, the matched-nothing warning helper)
#


async def test_has_regular_output_under_empty_range(wfp: Workflow):
    """An empty directory (no matching File rows at all) has no regular output."""
    async with wfp.db:
        assert wfp.has_regular_output_under("out/") is False


async def test_has_regular_output_under_volatile_only(wfp: Workflow):
    """A directory whose only in-range output is VOLATILE has no regular output."""
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "touch out/vol.txt", vol_paths=["out/vol.txt"])
    async with wfp.db:
        assert wfp.has_regular_output_under("out/") is False


async def test_has_regular_output_under_matching_range(wfp: Workflow):
    """A directory with a regular (non-volatile) output inside is detected."""
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "prog", out_paths=["out/report.txt"])
    async with wfp.db:
        assert wfp.has_regular_output_under("out/") is True


async def test_has_regular_output_under_boundary_sibling_excluded(wfp: Workflow):
    """A sibling sharing the directory's prefix but not its slash boundary is excluded."""
    async with wfp.db:
        plan = wfp.find(Step, "./plan.py")
        wfp.define_step(plan, "prog", out_paths=["out_debug.txt"])
    async with wfp.db:
        assert wfp.has_regular_output_under("out/") is False
