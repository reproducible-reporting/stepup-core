# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Test a few StepUp basic scenarios."""

import re

import pytest
from path import Path

from stepup.core.enums import Need
from stepup.core.exceptions import RPCError
from stepup.core.hash import FileHash
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
        await client("declare_missing")


async def test_wrong_type(client: AsyncRPCClient):
    with open("DONE.txt", "w") as fh:
        fh.write("done")
    with pytest.raises(RPCError):
        await client("declare_missing", 5)


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
           using_env = STEPUP_PATH_FILTER [amended]
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
           using_env = STEPUP_PATH_FILTER [amended]
             creator   root:
              source   file:plan.py
             product   file:foo

file:foo
               state = MISSING
             creator   step:./plan.py

"""


def _get_job_i() -> int:
    """Read the job_i of the currently running boot step, written by `BUILD_UNTIL_DONE`."""
    with open("STARTED.txt") as fh:
        return int(fh.read())


async def test_missing(client: AsyncRPCClient, path_tmp: Path):
    try:
        with open("foo", "w") as fh:
            fh.write("bar")
        to_check = await client("declare_missing", _get_job_i(), ["foo"])
    finally:
        with open("DONE.txt", "w") as fh:
            fh.write("done")
    await client("wait")
    prefix_graph = path_tmp / "graph"
    await client("graph", prefix_graph)
    assert to_check == [("foo", FileHash.unknown())]
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
           using_env = STEPUP_PATH_FILTER [amended]
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
        to_check = await client("declare_missing", job_i, ["original.txt"])
        assert to_check == [("original.txt", FileHash.unknown())]
        file_hash = FileHash.unknown().regen("original.txt")
        await client("confirm_hashes", [("original.txt", file_hash)])
    finally:
        with open("DONE.txt", "w") as fh:
            fh.write("done")
    await client("wait")
    prefix_graph = path_tmp / "graph"
    await client("graph", prefix_graph)
    _check_graph(prefix_graph + ".txt", COPY_GRAPH)
