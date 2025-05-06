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
"""Fixtures for testing StepUp."""

import asyncio
import contextlib
import hashlib
import os
import sqlite3
import stat
from collections.abc import AsyncGenerator, Iterator

import pytest
import pytest_asyncio
from path import Path

from stepup.core.director import serve
from stepup.core.enums import StepState
from stepup.core.file import File
from stepup.core.hash import FileHash
from stepup.core.reporter import ReporterClient
from stepup.core.rpc import AsyncRPCClient
from stepup.core.step import Step
from stepup.core.workflow import Workflow

pytest.register_assert_rewrite("stepup.core.pytest")

BOOT_UNTIL_DONE = """\
#!/usr/bin/env python3
from path import Path
from time import sleep
with open("STARTED.txt", "w") as fh:
    fh.write("started")
while not Path("DONE.txt").is_file():
    sleep(0.1)
print("Found DONE.txt. Stopping.")
"""


@pytest_asyncio.fixture()
async def client(tmpdir) -> AsyncGenerator[AsyncRPCClient, None]:
    # Launch stepup in background
    with contextlib.chdir(tmpdir):
        dir_stepup = Path(".stepup").absolute()
        dir_sockects = dir_stepup / "sockets"
        dir_sockects.makedirs_p()
        director_socket_path = dir_sockects / "director"

        with open("plan.py", "w") as fh:
            fh.write(BOOT_UNTIL_DONE)
        os.chmod("plan.py", stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
        reporter = ReporterClient()
        director = asyncio.create_task(
            serve(director_socket_path, 1, reporter, False, False, True, False)
        )
        while not director_socket_path.exists():
            await asyncio.sleep(0.1)
        while not Path("STARTED.txt").is_file():
            await asyncio.sleep(0.1)
        async with await AsyncRPCClient.socket(director_socket_path) as result:
            try:
                yield result
            finally:
                await result("join")
        await director


@pytest.fixture
def path_tmp(tmpdir: str) -> Path:
    return Path(tmpdir)


def fake_hash(path):
    digest = b"d" if path.endswith("/") else hashlib.blake2b(path.encode("utf8")).digest()
    mtime = sum(bytearray(digest)) ** 0.5
    mode = 0o755 if path.endswith("/") else 0o644
    return FileHash(digest, mode, mtime, len(path) ** 2, len(path))


def declare_static(workflow, creator, paths):
    """Declare a list of static files and confirm them.

    This a heavily simplified version of the stepup.core.api.static function.
    This is solely used for testing the workflow.
    """
    missing = workflow.declare_missing(creator, paths)
    checked = [(path, fake_hash(path)) for path, _ in missing]
    workflow.update_file_hashes(checked, "confirmed")
    return [workflow.find(File, path) for path in paths]


@pytest.fixture
def wfs() -> Iterator[Workflow]:
    """A workflow from scratch, no plan.py"""
    workflow = Workflow(sqlite3.Connection(":memory:"))
    declare_static(workflow, workflow.root, ["./"])
    yield workflow
    workflow.check_consistency()


@pytest.fixture
def wfp() -> Iterator[Workflow]:
    """A workflow with a boots step plan.py"""
    workflow = Workflow(sqlite3.Connection(":memory:"))
    with workflow.con:
        # Prepare the basic workflow with a plan script.
        root = workflow.root
        declare_static(workflow, root, ["./", "plan.py"])
        workflow.define_step(root, "./plan.py", inp_paths=["plan.py"])

        # Check the basics of the workflow.
        plan = workflow.find(Step, "./plan.py")
        nodes = list(workflow.nodes())
        assert nodes[0] == root
        for node in nodes[1:3]:
            assert isinstance(node, File)
        assert nodes[-1] == plan

        # Simulate running the plan
        job = workflow.job_queue.get_nowait()
        assert job.name == "EXECUTE: ./plan.py"
        assert job.pool is None
        plan.set_state(StepState.RUNNING)

    yield workflow
    with workflow.con:
        workflow.check_consistency()
