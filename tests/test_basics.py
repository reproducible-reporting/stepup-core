# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Test a few StepUp basic scenarios."""

import os
import re

import pytest
from path import Path

from stepup.core.enums import Need
from stepup.core.exceptions import GraphError, RPCError
from stepup.core.rpc import AsyncRPCClient


async def test_unknown_instruction(client: AsyncRPCClient):
    with open("DONE.txt", "w") as fh:
        fh.write("done")
    with pytest.raises(RPCError):
        await client("instruction_that_does_not_exist")


async def test_missing_argument(client: AsyncRPCClient):
    with open("DONE.txt", "w") as fh:
        fh.write("done")
    with pytest.raises(RPCError):
        await client("static")


async def test_wrong_type(client: AsyncRPCClient):
    with open("DONE.txt", "w") as fh:
        fh.write("done")
    with pytest.raises(RPCError):
        await client("static", 5)


FROM_SCRATCH_GRAPH = """\
root:
             product   file:plan.py
             product   step:./plan.py

file:plan.py
               state = STATIC
             creator   root:
                sink   step:./plan.py

step:./plan.py
               state = SUCCEEDED
                need = PLAN
           using_env = STEPUP_PATH_FILTER [dynamic]
             creator   root:
              source   file:plan.py

"""


def _check_graph(path, expected):
    with open(path) as fh:
        cur = fh.read()
        cur = re.sub(r" {10}(inp_|out_| {4})digest = ([ 0-9a-f]{71}|same)\n", "", cur)
        assert cur == expected


async def test_from_scratch(client: AsyncRPCClient, path_tmp: Path):
    with open("DONE.txt", "w") as fh:
        fh.write("done")
    await client("wait")
    prefix_graph = path_tmp / "graph"
    await client("graph", prefix_graph)
    _check_graph(prefix_graph + ".txt", FROM_SCRATCH_GRAPH)


STATIC_GRAPH = """\
root:
             product   file:plan.py
             product   step:./plan.py

file:plan.py
               state = STATIC
             creator   root:
                sink   step:./plan.py

step:./plan.py
               state = SUCCEEDED
                need = PLAN
           using_env = STEPUP_PATH_FILTER [dynamic]
             creator   root:
              source   file:plan.py
             product   file:foo

file:foo
               state = STATIC
             creator   step:./plan.py

"""


def _get_job_i() -> int:
    """Read the job_i of the currently running boot step, written by `BUILD_UNTIL_DONE`."""
    with open("STARTED.txt") as fh:
        return int(fh.read())


async def test_static(client: AsyncRPCClient, path_tmp: Path):
    try:
        with open("foo", "w") as fh:
            fh.write("bar")
        result = await client("static", _get_job_i(), [], ["foo"], [])
        assert result is None
    finally:
        with open("DONE.txt", "w") as fh:
            fh.write("done")
    await client("wait")
    prefix_graph = path_tmp / "graph"
    await client("graph", prefix_graph)
    _check_graph(prefix_graph + ".txt", STATIC_GRAPH)


COPY_GRAPH = """\
root:
             product   file:plan.py
             product   step:./plan.py

file:plan.py
               state = STATIC
             creator   root:
                sink   step:./plan.py

step:./plan.py
               state = SUCCEEDED
                need = PLAN
           using_env = STEPUP_PATH_FILTER [dynamic]
             creator   root:
              source   file:plan.py
             product   file:original.txt
             product   step:cp -v original.txt copy.txt

step:cp -v original.txt copy.txt
               state = SUCCEEDED
                need = DEFAULT
             creator   step:./plan.py
              source   file:original.txt
             product   file:copy.txt
                sink   file:copy.txt

file:original.txt
               state = STATIC
             creator   step:./plan.py
                sink   step:cp -v original.txt copy.txt

file:copy.txt
               state = BUILT
             creator   step:cp -v original.txt copy.txt
              source   step:cp -v original.txt copy.txt

"""


async def test_copy(client: AsyncRPCClient, path_tmp: Path):
    try:
        with open("original.txt", "w") as fh:
            fh.write("Hello world!")
        job_i = _get_job_i()
        await client(
            "step",
            job_i,
            "cp -v original.txt copy.txt",
            ["original.txt"],
            {},
            ["copy.txt"],
            [],
            ".",
            Need.DEFAULT.value,
            {},
            True,
        )
        result = await client("static", job_i, [], ["original.txt"], [])
        assert result is None
    finally:
        with open("DONE.txt", "w") as fh:
            fh.write("done")
    await client("wait")
    prefix_graph = path_tmp / "graph"
    await client("graph", prefix_graph)
    _check_graph(prefix_graph + ".txt", COPY_GRAPH)


async def test_static_registers_tree_before_file(client: AsyncRPCClient, path_tmp: Path):
    """One `static` RPC must register a tree before a file it contains.

    This is the canonical same-creator case: `Director.static` groups directories
    before files within a single call, so the tree is always registered first and
    hands the file over to itself, regardless of argument order.

    A consuming step is added for `sub/data.txt` so it has a sink: `Workflow.delete_detached()`
    detaches an unused static-tree file at the end of every build (see
    `test_static_tree_clean` in `test_workflow.py`), which would otherwise sweep away
    the very node this test means to inspect, hand-over or not.
    """
    try:
        job_i = _get_job_i()
        os.mkdir("sub")
        with open("sub/data.txt", "w") as fh:
            fh.write("hello")
        result = await client("static", job_i, ["sub/"], ["sub/data.txt"], [])
        assert result is None
        await client(
            "step",
            job_i,
            "cat sub/data.txt",
            ["sub/data.txt"],
            {},
            [],
            [],
            ".",
            Need.DEFAULT.value,
            {},
            True,
        )
    finally:
        with open("DONE.txt", "w") as fh:
            fh.write("done")
    await client("wait")
    prefix_graph = path_tmp / "graph"
    await client("graph", prefix_graph)
    with open(prefix_graph + ".txt") as fh:
        graph = fh.read()
    # The tree is the file's owner: a node is created, with the tree as its creator.
    assert "file:sub/data.txt\n" in graph
    assert "             creator   st:sub/\n" in graph


async def test_amend_blocks_until_static_tree_match_confirmed(client: AsyncRPCClient):
    """`amend()` naming a fresh static-tree match must block until it is hashed and
    confirmed, then report the step as runnable with the file STATIC."""
    try:
        job_i = _get_job_i()
        os.mkdir("sub")
        with open("sub/data.txt", "w") as fh:
            fh.write("hello")
        await client("static", job_i, ["sub/"], [], [])
        carry_on = await client("amend", job_i, ["sub/data.txt"], [], [], [])
        assert carry_on is True
    finally:
        with open("DONE.txt", "w") as fh:
            fh.write("done")
    await client("wait")


async def test_amend_reports_missing_static_tree_match(client: AsyncRPCClient):
    """`amend()` naming a static-tree match that does not exist on disk must not block
    forever, and must report the step as not runnable (carry_on=False)."""
    try:
        job_i = _get_job_i()
        os.mkdir("sub")
        await client("static", job_i, ["sub/"], [], [])
        carry_on = await client("amend", job_i, ["sub/ghost.txt"], [], [], [])
        assert carry_on is False
    finally:
        with open("DONE.txt", "w") as fh:
            fh.write("done")
    await client("wait")


async def test_hold_release_rpc_smoke(client: AsyncRPCClient):
    """`hold()`/`release()` RPCs round-trip: children declared while holding still run."""
    try:
        job_i = _get_job_i()
        await client("hold", job_i)
        await client(
            "step", job_i, "touch a.txt", [], {}, ["a.txt"], [], ".", Need.DEFAULT.value, {}, True
        )
        await client(
            "step", job_i, "touch b.txt", [], {}, ["b.txt"], [], ".", Need.DEFAULT.value, {}, True
        )
        await client("release", job_i)
    finally:
        with open("DONE.txt", "w") as fh:
            fh.write("done")
    await client("wait")
    assert Path("a.txt").is_file()
    assert Path("b.txt").is_file()


async def test_hold_nested_rpc_smoke(client: AsyncRPCClient):
    """A second `hold()` without an intervening `release()` is a re-entrant nested hold, not
    an error: it takes two matching `release()` calls to round-trip cleanly. See
    `test_hold_release_rpc_smoke` for the non-nested case, and the `hold_nested` example for
    proof that children stay held until the outermost `release()`.
    """
    try:
        job_i = _get_job_i()
        await client("hold", job_i)
        await client("hold", job_i)
        await client("release", job_i)
        await client("release", job_i)
    finally:
        with open("DONE.txt", "w") as fh:
            fh.write("done")
    await client("wait")


async def test_release_without_hold_raises_graph_error(client: AsyncRPCClient):
    """`release()` with no matching `hold()` raises rather than corrupting scheduler state.

    The director's `GraphError` is a `UsageError`, so the client re-raises that same class
    instead of wrapping it in an `RPCError`.
    That requires `STEPUP_DEBUG` to be unset, which the `_unset_stepup_debug` fixture
    in `conftest.py` guarantees.
    """
    try:
        job_i = _get_job_i()
        with pytest.raises(GraphError):
            await client("release", job_i)
    finally:
        with open("DONE.txt", "w") as fh:
            fh.write("done")
    await client("wait")
