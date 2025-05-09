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
"""Test a few StepUp basic scenarios."""

import re

import pytest
from path import Path

from stepup.core.exceptions import RPCError
from stepup.core.hash import FileHash
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
        await client("missing")


@pytest.mark.asyncio
async def test_wrong_type(client: AsyncRPCClient):
    with open("DONE.txt", "w") as fh:
        fh.write("done")
    with pytest.raises(RPCError):
        await client("missing", 5)


FROM_SCRATCH_GRAPH = """\
root:
             creates   file:./
             creates   file:plan.py
             creates   step:runpy ./plan.py

file:./
               state = STATIC
          created by   root:
            supplies   file:plan.py
            supplies   step:runpy ./plan.py

file:plan.py
               state = STATIC
          created by   root:
            consumes   file:./
            supplies   step:runpy ./plan.py

step:runpy ./plan.py
               state = SUCCEEDED
          created by   root:
            consumes   file:./
            consumes   file:plan.py

"""


def _check_graph(path, expected):
    with open(path) as fh:
        cur = fh.read()
        cur = re.sub(
            r" {10}(inp_|out_| {4})digest = ([ 0-9a-f]{71}\n {21}= [ 0-9a-f]{71}|same)\n", "", cur
        )
        assert cur == expected


@pytest.mark.asyncio
async def test_from_scratch(client: AsyncRPCClient, path_tmp: Path):
    with open("DONE.txt", "w") as fh:
        fh.write("done")
    await client("wait")
    prefix_graph = path_tmp / "graph"
    await client("graph", prefix_graph)
    _check_graph(prefix_graph + ".txt", FROM_SCRATCH_GRAPH)


STATIC_GRAPH = """\
root:
             creates   file:./
             creates   file:plan.py
             creates   step:runpy ./plan.py

file:./
               state = STATIC
          created by   root:
            supplies   file:foo
            supplies   file:plan.py
            supplies   step:runpy ./plan.py

file:plan.py
               state = STATIC
          created by   root:
            consumes   file:./
            supplies   step:runpy ./plan.py

step:runpy ./plan.py
               state = SUCCEEDED
          created by   root:
            consumes   file:./
            consumes   file:plan.py
             creates   file:foo

file:foo
               state = MISSING
          created by   step:runpy ./plan.py
            consumes   file:./

"""


@pytest.mark.asyncio
async def test_missing(client: AsyncRPCClient, path_tmp: Path):
    try:
        with open("foo", "w") as fh:
            fh.write("bar")
        to_check = await client("missing", 4, ["foo"])
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
             creates   file:./
             creates   file:plan.py
             creates   step:runpy ./plan.py

file:./
               state = STATIC
          created by   root:
            supplies   file:copy.txt
            supplies   file:original.txt
            supplies   file:plan.py
            supplies   step:runpy ./plan.py
            supplies   step:runsh cp -v original.txt copy.txt

file:plan.py
               state = STATIC
          created by   root:
            consumes   file:./
            supplies   step:runpy ./plan.py

step:runpy ./plan.py
               state = SUCCEEDED
          created by   root:
            consumes   file:./
            consumes   file:plan.py
             creates   file:original.txt
             creates   step:runsh cp -v original.txt copy.txt

step:runsh cp -v original.txt copy.txt
               state = SUCCEEDED
          created by   step:runpy ./plan.py
            consumes   file:./
            consumes   file:original.txt
             creates   file:copy.txt
            supplies   file:copy.txt

file:original.txt
               state = STATIC
          created by   step:runpy ./plan.py
            consumes   file:./
            supplies   step:runsh cp -v original.txt copy.txt

file:copy.txt
               state = BUILT
          created by   step:runsh cp -v original.txt copy.txt
            consumes   file:./
            consumes   step:runsh cp -v original.txt copy.txt

"""


@pytest.mark.asyncio
async def test_copy(client: AsyncRPCClient, path_tmp: Path):
    try:
        with open("original.txt", "w") as fh:
            fh.write("Hello world!")
        await client(
            "step",
            4,
            "runsh cp -v original.txt copy.txt",
            ["original.txt"],
            {},
            ["copy.txt"],
            [],
            "./",
            False,
            None,
            False,
        )
        to_check = await client("missing", 4, ["original.txt"])
        assert to_check == [("original.txt", FileHash.unknown())]
        file_hash = FileHash.unknown().regen("original.txt")
        await client("confirm", [("original.txt", file_hash)])
    finally:
        with open("DONE.txt", "w") as fh:
            fh.write("done")
    await client("wait")
    prefix_graph = path_tmp / "graph"
    await client("graph", prefix_graph)
    _check_graph(prefix_graph + ".txt", COPY_GRAPH)
