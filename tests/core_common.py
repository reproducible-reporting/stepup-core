# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Utilities used by multiple test modules."""

import asyncio
import concurrent.futures
import hashlib
import os
import socket
import subprocess
import sys
import threading
from collections.abc import Callable
from typing import Self, TypeVar

import attrs
import pytest

from stepup.core.enums import HashUpdateCause
from stepup.core.exceptions import CyclicError
from stepup.core.graph.file import File
from stepup.core.graph.sqlite3 import DBSession
from stepup.core.graph.step import Step
from stepup.core.hash import FileHash
from stepup.core.path import Path
from stepup.core.rpc.server import TCP_HOST, TOKEN_SIZE, allow_rpc
from stepup.core.rpc.transport import TCPEndpoint

if sys.platform == "win32":
    from stepup.core.platform.winapi import JobObject


async def settled_task_names() -> list[str]:
    """The names of the tasks that are still pending, after letting the event loop settle.

    A few no-op sleeps are needed because a cancelled task only completes
    when the event loop gets to deliver the cancellation.
    """
    for _ in range(5):
        await asyncio.sleep(0)
    current = asyncio.current_task()
    return sorted(task.get_name() for task in asyncio.all_tasks() if task is not current)


def create_socket_path(path: Path, transport: str):
    """Create what a server on `transport` creates at its socket path, without a server.

    A client finds the path, but nobody listening behind it.
    """
    if transport == "unix":
        with socket.socket(socket.AF_UNIX) as sock:
            sock.bind(path)
    else:
        TCPEndpoint(TCP_HOST, 1, bytes(TOKEN_SIZE)).write(path)


@attrs.define
class EchoHandler:
    """A simple handler for unit testing the RPC module in StepUp."""

    name: str = attrs.field()
    stop_event: asyncio.Event = attrs.field(init=False, factory=asyncio.Event)

    @allow_rpc
    async def echo(self, msg: str, delay: float = 0.0) -> str:
        if delay > 0:
            await asyncio.sleep(delay)
        return f"{self.name}: {msg}"

    @allow_rpc
    def lcg(self, seed, modulus=71, multiplier=45, increment=91) -> int:
        """Implementation of a linear congruential generator iteration.

        See https://en.wikipedia.org/wiki/Linear_congruential_generator
        """
        return (multiplier * seed + increment) % modulus

    @allow_rpc
    def greet(self, name: str) -> str:
        """Say hello to `name`, a parameter that the client's `__call__` also has."""
        return f"{self.name}: hello {name}"

    @allow_rpc
    def raise_usage(self):
        """Fail with a mistake the caller can fix, i.e. a `UsageError` subclass."""
        raise CyclicError("cyclic")

    @allow_rpc
    def raise_internal(self):
        """Fail with something that indicates a bug in the server."""
        raise RuntimeError("bug")

    @allow_rpc
    def shutdown(self):
        self.stop_event.set()

    def not_allowed(self):
        print("This method should not be callable.")


@attrs.define
class ConsoleHost:
    """A program that runs in a Windows console of its own, with the test as its user.

    The work is done by the script `console_host.py`, which documents the actions.
    The script and everything it starts are members of one Job Object,
    so that `close` leaves nothing behind, whatever state a failed test is in.
    """

    process: subprocess.Popen = attrs.field()
    """The process of `console_host.py`, which ends with the return code of the program."""

    job: "JobObject" = attrs.field()
    """The job that holds the script, the program and all their descendants."""

    path_output: Path = attrs.field()
    """The file that collects the standard output and error of the program."""

    @classmethod
    def start(cls, argv: list[str], cwd: Path, path_output: Path) -> Self:
        """Start a program in a new console, which has no visible window.

        Parameters
        ----------
        argv
            The command line of the program.
        cwd
            The working directory of the program.
        path_output
            The file to which the program writes its standard output and error.
        """
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        process = subprocess.Popen(
            [sys.executable, Path(__file__).parent / "console_host.py", path_output, *argv],
            cwd=cwd,
            stdin=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            creationflags=subprocess.CREATE_NEW_CONSOLE,
            startupinfo=startupinfo,
        )
        job = JobObject.create()
        job.assign(process.pid)
        return cls(process, job, path_output)

    def act(self, action: str) -> None:
        """Let the user of the console do something, e.g. `"ctrl-c"` or `"keys q"`."""
        self.process.stdin.write(f"{action}\n")
        self.process.stdin.flush()

    def wait(self, timeout: float) -> int:
        """Wait until the program exits on its own, and return its return code.

        Raises
        ------
        AssertionError
            If the program is still running after `timeout` seconds.
        """
        self.process.stdin.close()
        try:
            return self.process.wait(timeout)
        except subprocess.TimeoutExpired:
            raise AssertionError(
                f"The program did not exit within {timeout} s. Its output:\n{self.output()}"
            ) from None

    def output(self) -> str:
        """Return what the program has written so far."""
        try:
            return self.path_output.read_text(encoding="utf-8")
        except FileNotFoundError:
            # The program has not been started yet.
            return ""

    def close(self) -> None:
        """End everything that is still running in the console."""
        self.job.terminate(1)
        self.job.close()
        self.process.stdin.close()
        self.process.wait()


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


STUCK_DEADLINE = 5.0
"""Seconds within which a `StuckWork` must start or end, well within the budget of a test."""


class StuckWork:
    """The work of a `ThreadWorker` that ignores its cancel event, like a stuck system call.

    It returns `"released"` or raises `error` once `release` is set.
    Use it as a context manager, which sets `release` at the end and joins the thread,
    so that the thread does not outlive the test.
    """

    def __init__(self, error: Exception | None = None):
        self.error = error
        self.started = threading.Event()
        self.release = threading.Event()
        self.thread: threading.Thread | None = None

    def __call__(self, *args) -> str:
        self.thread = threading.current_thread()
        self.started.set()
        self.release.wait(2 * STUCK_DEADLINE)
        if self.error is not None:
            raise self.error
        return "released"

    async def wait_started(self) -> None:
        """Wait until the work runs in its thread."""
        assert await asyncio.to_thread(self.started.wait, STUCK_DEADLINE)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info) -> None:
        self.release.set()
        if self.thread is not None:
            self.thread.join(STUCK_DEADLINE)
            assert not self.thread.is_alive()


FIFO_DEADLINE = 5.0
"""Seconds after which a call counts as blocked on a FIFO, well within the budget of a test."""

T = TypeVar("T")


def call_with_fifo_guard(fifo: str, func: Callable[..., T], *args) -> T:
    """Call a function in a thread, and fail when it waits for the other side of a FIFO.

    A test that would hang when a regression makes `func` wait for the FIFO
    then fails with its own message, instead of on the timeout of the whole test.
    The thread is released by opening both sides of the FIFO, so that it does not outlive the test.

    Returns
    -------
    result
        The result of `func`, whose exception is raised again.
    """
    with concurrent.futures.ThreadPoolExecutor(1) as pool:
        future = pool.submit(func, *args)
        try:
            return future.result(timeout=FIFO_DEADLINE)
        except TimeoutError:
            # Opening for reading does not wait with `O_NONBLOCK`,
            # and it lets the opening for writing succeed.
            fd_read = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
            try:
                os.close(os.open(fifo, os.O_WRONLY | os.O_NONBLOCK))
            finally:
                os.close(fd_read)
            pytest.fail(f"Waited more than {FIFO_DEADLINE} s for the other side of {fifo}")
