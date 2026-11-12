# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Fixtures for testing StepUp."""

import asyncio
import contextlib
import hashlib
import os
import stat
import tempfile
import threading
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable, Iterator

import attrs
import pytest
import pytest_asyncio

from stepup.core.constants import GRAPH_DB
from stepup.core.director import ServeConfig, serve
from stepup.core.enums import HashUpdateCause, Need
from stepup.core.file import File
from stepup.core.hash import FileHash
from stepup.core.oscompat import (
    af_unix_available,
    forkserver_available,
    raw_terminal_available,
    suspend_available,
)
from stepup.core.path import Path
from stepup.core.popen import find_shell
from stepup.core.reporter import ReporterClient
from stepup.core.rpc import SocketAsyncRPCClient, select_rpc_transport
from stepup.core.sqlite3 import DBSession
from stepup.core.step import Step
from stepup.core.workflow import Workflow

pytest.register_assert_rewrite("stepup.core.pytest")


def _forkserver_enabled() -> bool:
    """Whether the forkserver is available and not switched off for this test run."""
    return forkserver_available() and os.environ.get("STEPUP_BUILD_FORKSERVER") != "0"


CAPABILITY_MARKERS = {
    "requires_af_unix": (af_unix_available, "requires Unix domain sockets (socket.AF_UNIX)"),
    "requires_forkserver": (
        _forkserver_enabled,
        "requires the forkserver (available on this platform and STEPUP_BUILD_FORKSERVER != 0)",
    ),
    "requires_termios": (raw_terminal_available, "requires POSIX terminal control (termios)"),
    "requires_suspend": (suspend_available, "requires the job-control signals of Ctrl-Z"),
}
"""Markers that skip a test when a capability is missing, with the predicate testing for it.

Each marker must also be registered under `markers` in `pyproject.toml`.
"""


def pytest_collection_modifyitems(items):
    for name, (available, reason) in CAPABILITY_MARKERS.items():
        if available():
            continue
        skip = pytest.mark.skip(reason=reason)
        for item in items:
            if item.get_closest_marker(name):
                item.add_marker(skip)


@pytest.fixture(autouse=True)
def _unset_stepup_debug(monkeypatch: pytest.MonkeyPatch):
    """Pin `STEPUP_DEBUG` off, because unit tests inherit the developer's environment.

    Several code paths read the variable at call time
    (`_raise_remote_error` in `rpc.py`, `_shorten` in `tracebacks.py`),
    and `docs/development.md` recommends exporting it while working on StepUp,
    so without this fixture those tests would assert different things for different people.
    Tests that exercise the debug path set the variable themselves.
    The examples are unaffected either way:
    `run_example` passes `STEPUP_DEBUG=1` in the child environment regardless.
    """
    monkeypatch.delenv("STEPUP_DEBUG", raising=False)


BUILD_UNTIL_DONE = """\
#!/usr/bin/env python3
import os
from stepup.core.path import Path
from time import sleep
with open("STARTED.txt", "w") as fh:
    fh.write(os.environ["STEPUP_JOB_I"])
while not Path("DONE.txt").is_file():
    sleep(0.1)
print("Found DONE.txt. Stopping.")
"""


SHUTDOWN_TIMEOUT = 10.0
"""Seconds the `client` fixture gives the director to stop before failing the test.

The teardown must fail instead of blocking,
because `pytest-timeout` disarms the timeout of a test as soon as it reports a failure,
which leaves the teardown of a test whose setup failed completely unguarded.
"""


@pytest.fixture(params=[pytest.param("unix", marks=pytest.mark.requires_af_unix), "tcp"])
def rpc_transport(request: pytest.FixtureRequest) -> str:
    """Each transport of `stepup.core.rpc`, for a test that must cover both explicitly.

    A test that only needs some working transport calls `select_rpc_transport()` instead,
    which follows `STEPUP_RPC_TRANSPORT`, so that the test suite can be run with either one.
    """
    return request.param


@pytest.fixture
def fresh_shell_cache() -> Iterator[None]:
    """Make `find_shell` search again in this test, and forget what it finds afterwards.

    A test that changes `STEPUP_SHELL` needs this,
    because the shell is remembered for the lifetime of the process.
    """
    find_shell.cache_clear()
    yield
    find_shell.cache_clear()


@pytest.fixture
def dir_sockets() -> Iterator[Path]:
    """Provide a directory for socket paths, short enough for a Unix domain socket on macOS.

    macOS limits the path of a socket to 104 bytes,
    which the paths under pytest's temporary directories exceed there.
    The directory is created in the same way as the one `stepup build` puts its sockets in.
    """
    with tempfile.TemporaryDirectory(prefix="stepup-") as path:
        yield Path(path)


@pytest_asyncio.fixture
async def client(tmpdir, dir_sockets) -> AsyncGenerator[SocketAsyncRPCClient, None]:
    # Launch stepup in background
    with contextlib.chdir(tmpdir):
        Path(".stepup").makedirs_p()
        director_socket_path = dir_sockets / "director"

        with open("plan.py", "w", encoding="utf-8", newline="\n") as fh:
            fh.write(BUILD_UNTIL_DONE)
        os.chmod("plan.py", stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
        # The DBSession owns the SQLite connection for the lifetime of the director task,
        # mirroring `with DBSession.open(GRAPH_DB) as db` in `director.main`.
        async with ReporterClient() as reporter:
            with DBSession.open(GRAPH_DB) as db:
                director = asyncio.create_task(
                    serve(
                        ServeConfig(njob=1, use_duration=False, do_watch=True),
                        director_socket_path=director_socket_path,
                        rpc_transport=select_rpc_transport(),
                        reporter=reporter,
                        db=db,
                        # Do not hijack the signal handlers of the pytest process.
                        handle_signals=False,
                    )
                )
                while not director_socket_path.exists():
                    await asyncio.sleep(0.1)
                while not Path("STARTED.txt").is_file():
                    await asyncio.sleep(0.1)
                async with SocketAsyncRPCClient(director_socket_path) as result:
                    try:
                        yield result
                    finally:
                        # The boot script stops as soon as this file exists.
                        # It is also written here because a test that never reached it,
                        # e.g. because one of its other fixtures raised,
                        # would leave `wait_and_shutdown` waiting for a step that never ends.
                        Path("DONE.txt").touch()
                        async with asyncio.timeout(SHUTDOWN_TIMEOUT):
                            await result("wait_and_shutdown")
                async with asyncio.timeout(SHUTDOWN_TIMEOUT):
                    await director


@pytest.fixture
def path_tmp(tmpdir: str) -> Path:
    return Path(tmpdir)


@pytest.fixture
def clean_env(monkeypatch, path_tmp):
    """Hide the developer's own configuration, so it cannot reach the output under test.

    Both the StepUp environment variables and `~/.config/stepup.toml` are put out of reach.
    The system-wide `/etc/stepup.toml` is the one config file that cannot be hidden this way.
    """
    for name in list(os.environ):
        if name.startswith("STEPUP_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("HOME", path_tmp)


def fake_hash(path):
    digest = b"d" if path.endswith("/") else hashlib.sha256(path.encode("utf8")).digest()
    mtime = sum(bytearray(digest)) ** 0.5
    mode = 0o755 if path.endswith("/") else 0o644
    return FileHash(digest, mode, len(path) ** 2, mtime, len(path))


def chmod(file_hash: FileHash) -> FileHash:
    """Return `file_hash` with a different mode, as a file change that leaves the content alone.

    The graph printout shows only the digest,
    so a test can use this to change a file without invalidating a literal graph it compares to.
    """
    return attrs.evolve(file_hash, mode=file_hash.mode ^ 0o111)


def declare_static(workflow, creator, paths):
    """Declare a list of static files and confirm them.

    This a heavily simplified version of the stepup.core.api.static function.
    This is solely used for testing the workflow.
    """
    unconfirmed = workflow.declare_static_files(creator, paths)
    for path in unconfirmed:
        workflow.update_file_hash(path, fake_hash(path), cause=HashUpdateCause.OBSERVED)
    return [workflow.find(File, path) for path in paths]


def amend_step(workflow, step, **kwargs):
    """Call `Workflow.amend_step`, defaulting `ran_concurrently` to never overlapping.

    Most tests don't exercise the freshness check, so they have no real
    `Scheduler.ran_concurrently` to pass. This is solely used for testing the workflow.
    """
    kwargs.setdefault("ran_concurrently", lambda producer_i, consumer_i: False)
    return workflow.amend_step(step, **kwargs)


async def get_duration_and_tail_time(db: DBSession, step: Step) -> tuple[float, float, int]:
    async with db:
        duration, tail_time, check_after = db.execute(
            "SELECT duration, _tail_time, _check_after FROM step WHERE node = ?", (step.i,)
        ).fetchone()
    return duration, tail_time, check_after


@pytest_asyncio.fixture
async def wfs_factory() -> AsyncIterator[Callable[..., Awaitable[Workflow]]]:
    """Create workflows from scratch, no plan.py, and tear all of them down together.

    A test calls the factory more than once when it needs a second, independent workflow,
    e.g. to replay the same declarations in the reverse order:
    the first order has already put the conflicting declaration in the graph,
    so only a fresh workflow can carry the exact same labels.
    """
    workflows = []
    with contextlib.ExitStack() as stack:

        async def _make(defer_cap: int = 100) -> Workflow:
            # The connection is opened for the fixture lifetime.
            # Tests using this fixture can use `async with workflow.db:`
            # to acquire the lock for the duration of their test.
            db = stack.enter_context(DBSession.open(":memory:"))
            dir_queue = asyncio.Queue()
            workflow = Workflow(db, dir_queue=dir_queue, defer_cap=defer_cap)
            await workflow.initialize()
            workflows.append(workflow)
            return workflow

        yield _make

        for workflow in workflows:
            async with workflow.db:
                workflow._check_consistency()


@pytest_asyncio.fixture
async def wfs(request, wfs_factory) -> Workflow:
    """A single workflow from scratch, no plan.py

    Supports indirect parametrization to override `defer_cap`, e.g.
    `@pytest.mark.parametrize("wfs", [3], indirect=True)`.
    """
    return await wfs_factory(defer_cap=getattr(request, "param", 100))


@pytest_asyncio.fixture
async def wfp_factory(wfs_factory) -> Callable[..., Awaitable[Workflow]]:
    """Create workflows with a boot step plan.py, sharing the teardown of `wfs_factory`."""

    async def _make(defer_cap: int = 100) -> Workflow:
        workflow = await wfs_factory(defer_cap=defer_cap)
        async with workflow.db:
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
        return workflow

    return _make


@pytest_asyncio.fixture
async def wfp(wfp_factory) -> Workflow:
    """A single workflow with a boot step plan.py"""
    return await wfp_factory()


class TrippingEvent(threading.Event):
    """A cancel event whose `is_set` starts returning True after `trip_after` polls."""

    def __init__(self, trip_after: int):
        super().__init__()
        self.trip_after = trip_after
        self.polls = 0

    def is_set(self) -> bool:
        self.polls += 1
        if self.polls > self.trip_after:
            self.set()
        return super().is_set()
