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
"""Test a few StepUp basic scenarios."""

import re

import pytest
from path import Path

from stepup.core.exceptions import RPCError
from stepup.core.rpc import AsyncRPCClient


@pytest.mark.asyncio
async def test_unknown_instruction(client: AsyncRPCClient):
    with open("DONE.txt", "w") as fh:
        fh.write("done")
    with pytest.raises(RPCError):
        await client("instruction_that_does_not_exist")


@pytest.mark.asyncio
async def test_missing_argument(client: AsyncRPCClient):
    with open("DONE.txt", "w") as fh:
        fh.write("done")
    with pytest.raises(RPCError):
        await client("static")


@pytest.mark.asyncio
async def test_wrong_type(client: AsyncRPCClient):
    with open("DONE.txt", "w") as fh:
        fh.write("done")
    with pytest.raises(RPCError):
        await client("static", 5)


FROM_SCRATCH_GRAPH = """\
root:
             version = v1
             creates   file:./
             creates   file:plan.py
             creates   step:./plan.py

file:plan.py
                path = plan.py
               state = STATIC
          created by   root:
            consumes   file:./
            supplies   step:./plan.py

file:./
                path = ./
               state = STATIC
          created by   root:
            supplies   file:plan.py
            supplies   step:./plan.py

step:./plan.py
             workdir = ./
             command = ./plan.py
               state = SUCCEEDED
          created by   root:
            consumes   file:./
            consumes   file:plan.py

"""


def _check_graph(path, expected):
    with open(path) as fh:
        cur = fh.read()
        cur = re.sub(r" {10}(inp_| {4})digest = [ 0-9a-f]{71}\n {21}= [ 0-9a-f]{71}\n", "", cur)
        assert cur == expected


@pytest.mark.asyncio
async def test_from_scratch(client: AsyncRPCClient, tmpdir: str):
    with open("DONE.txt", "w") as fh:
        fh.write("done")
    await client("wait")
    prefix_graph = Path(tmpdir) / "graph"
    await client("graph", prefix_graph)
    _check_graph(prefix_graph + ".txt", FROM_SCRATCH_GRAPH)


STATIC_GRAPH = """\
root:
             version = v1
             creates   file:./
             creates   file:plan.py
             creates   step:./plan.py

file:plan.py
                path = plan.py
               state = STATIC
          created by   root:
            consumes   file:./
            supplies   step:./plan.py

file:./
                path = ./
               state = STATIC
          created by   root:
            supplies   file:foo
            supplies   file:plan.py
            supplies   step:./plan.py

step:./plan.py
             workdir = ./
             command = ./plan.py
               state = SUCCEEDED
          created by   root:
            consumes   file:./
            consumes   file:plan.py
             creates   file:foo

file:foo
                path = foo
               state = STATIC
          created by   step:./plan.py
            consumes   file:./

"""


@pytest.mark.asyncio
async def test_static(client: AsyncRPCClient, tmpdir: str):
    try:
        with open("foo", "w") as fh:
            fh.write("bar")
        step_key_plan = "step:./plan.py"
        await client("static", step_key_plan, ["foo"])
    finally:
        with open("DONE.txt", "w") as fh:
            fh.write("done")
    await client("wait")
    prefix_graph = Path(tmpdir) / "graph"
    await client("graph", prefix_graph)
    _check_graph(prefix_graph + ".txt", STATIC_GRAPH)


COPY_GRAPH = """\
root:
             version = v1
             creates   file:./
             creates   file:plan.py
             creates   step:./plan.py

file:plan.py
                path = plan.py
               state = STATIC
          created by   root:
            consumes   file:./
            supplies   step:./plan.py

file:./
                path = ./
               state = STATIC
          created by   root:
            supplies   file:copy.txt
            supplies   file:original.txt
            supplies   file:plan.py
            supplies   step:./plan.py
            supplies   step:cp -v original.txt copy.txt

step:./plan.py
             workdir = ./
             command = ./plan.py
               state = SUCCEEDED
          created by   root:
            consumes   file:./
            consumes   file:plan.py
             creates   file:original.txt
             creates   step:cp -v original.txt copy.txt

step:cp -v original.txt copy.txt
             workdir = ./
             command = cp -v original.txt copy.txt
               state = SUCCEEDED
          created by   step:./plan.py
            consumes   file:./
            consumes   file:original.txt
             creates   file:copy.txt
            supplies   file:copy.txt

file:original.txt
                path = original.txt
               state = STATIC
          created by   step:./plan.py
            consumes   file:./
            supplies   step:cp -v original.txt copy.txt

file:copy.txt
                path = copy.txt
               state = BUILT
          created by   step:cp -v original.txt copy.txt
            consumes   file:./
            consumes   step:cp -v original.txt copy.txt

"""


@pytest.mark.asyncio
async def test_copy(client: AsyncRPCClient, tmpdir: str):
    try:
        with open("original.txt", "w") as fh:
            fh.write("Hello world!")
        step_key_plan = "step:./plan.py"
        step_key_copy = await client(
            "step",
            step_key_plan,
            "cp -v original.txt copy.txt",
            ["original.txt"],
            {},
            ["copy.txt"],
            [],
            "./",
            False,
            None,
            False,
        )
        assert step_key_copy == "step:cp -v original.txt copy.txt"
        await client("static", step_key_plan, ["original.txt"])
    finally:
        with open("DONE.txt", "w") as fh:
            fh.write("done")
    await client("wait")
    prefix_graph = Path(tmpdir) / "graph"
    await client("graph", prefix_graph)
    _check_graph(prefix_graph + ".txt", COPY_GRAPH)
