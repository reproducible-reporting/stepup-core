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
"""Fixtures for testing StepUp."""

import asyncio
import contextlib
import os
import stat
from collections.abc import Iterator

import pytest
import pytest_asyncio
from path import Path

from stepup.core.director import serve
from stepup.core.reporter import ReporterClient
from stepup.core.rpc import AsyncRPCClient

pytest.register_assert_rewrite("stepup.core.pytest")

BOOT_UNTIL_DONE = """\
#!/usr/bin/env python
from path import Path
from time import sleep
while not Path("DONE.txt").is_file():
    sleep(0.1)
print("Found DONE.txt. Stopping.")
"""


@pytest_asyncio.fixture()
async def client(tmpdir) -> Iterator[AsyncRPCClient]:
    # Launch stepup in background
    with contextlib.chdir(tmpdir):
        dir_stepup = Path(".stepup").absolute()
        dir_sockects = dir_stepup / "sockets"
        dir_sockects.makedirs_p()
        dir_logs = dir_stepup / "logs"
        dir_logs.makedirs_p()
        director_socket_path = dir_sockects / "director"

        with open("plan.py", "w") as fh:
            fh.write(BOOT_UNTIL_DONE)
        os.chmod("plan.py", stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
        reporter = ReporterClient()
        director = asyncio.create_task(
            serve(director_socket_path, 1, "graph.mpk", "plan.py", reporter, False, False)
        )
        while not director_socket_path.exists():
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
