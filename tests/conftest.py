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
import stat
from collections.abc import AsyncGenerator, AsyncIterator

import pytest
import pytest_asyncio
from path import Path

from stepup.core.constants import GRAPH_DB
from stepup.core.director import serve
from stepup.core.enums import HashUpdateCause, Need
from stepup.core.file import File
from stepup.core.hash import FileHash
from stepup.core.reporter import ReporterClient
from stepup.core.rpc import AsyncRPCClient
from stepup.core.sqlite3 import DBSession
from stepup.core.step import Step
from stepup.core.workflow import Workflow

pytest.register_assert_rewrite("stepup.core.pytest")


def pytest_collection_modifyitems(items):
    if os.environ.get("STEPUP_BUILD_FORK_RUNPY") == "0":
        skip = pytest.mark.skip(
            reason="requires fork-based process execution (STEPUP_BUILD_FORK_RUNPY != 0)"
        )
        for item in items:
            if item.get_closest_marker("requires_fork_runpy"):
                item.add_marker(skip)


BUILD_UNTIL_DONE = """\
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
            fh.write(BUILD_UNTIL_DONE)
        os.chmod("plan.py", stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
        reporter = ReporterClient()
        # The DBSession owns the SQLite connection for the lifetime of the director task,
        # mirroring `with DBSession(GRAPH_DB) as db` in `director.main`.
        with DBSession.open(GRAPH_DB) as db:
            director = asyncio.create_task(
                serve(
                    director_socket_path=director_socket_path,
                    njob=1,
                    reporter=reporter,
                    do_clean=True,
                    use_duration=False,
                    explain_rerun=False,
                    fix_epoch=True,
                    show_perf=False,
                    do_watch=True,
                    do_watch_first=False,
                    available_resources=None,
                    db=db,
                )
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
    digest = b"d" if path.endswith("/") else hashlib.sha256(path.encode("utf8")).digest()
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
    workflow.update_file_hashes(checked, HashUpdateCause.CONFIRMED)
    return [workflow.find(File, path) for path in paths]


@pytest_asyncio.fixture
async def wfs() -> AsyncIterator[Workflow]:
    """A workflow from scratch, no plan.py"""
    dir_queue = asyncio.Queue()
    # The `with` opens the connection for the fixture lifetime.
    # Tests using this fixture can use `async with db:`
    # to acquire the lock for the duration of their test.
    with DBSession.open(":memory:") as db:
        workflow = Workflow(db, makedirs=False, dir_queue=dir_queue)
        await workflow.initialize()
        yield workflow
        async with db:
            workflow.check_consistency()


@pytest_asyncio.fixture
async def wfp() -> AsyncIterator[Workflow]:
    """A workflow with a boot step plan.py"""
    dir_queue = asyncio.Queue()
    with DBSession.open(":memory:") as db:
        workflow = Workflow(db, makedirs=False, dir_queue=dir_queue)
        await workflow.initialize()
        async with db:
            # Prepare the basic workflow with a plan script.
            root = workflow.root
            file_plan = declare_static(workflow, root, ["plan.py"])[0]
            workflow.define_step(root, "./plan.py", inp_paths=["plan.py"], need=Need.PLAN)

            # Check the basics of the workflow.
            step_plan = workflow.find(Step, "./plan.py")
            nodes = list(workflow.nodes())
            assert len(nodes) == 3
            assert nodes[0] == root
            assert nodes[1] == file_plan
            assert nodes[2] == step_plan

        yield workflow
        async with db:
            workflow.check_consistency()
