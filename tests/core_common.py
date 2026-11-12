# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Utilities used by multiple test modules."""

import asyncio
import socket
import subprocess
import sys
from typing import Self

import attrs

from stepup.core.exceptions import CyclicError
from stepup.core.path import Path
from stepup.core.rpc import TCP_HOST, TOKEN_SIZE, _TCPEndpoint, allow_rpc

if sys.platform == "win32":
    from stepup.core.winapi import JobObject


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
        _TCPEndpoint(TCP_HOST, 1, bytes(TOKEN_SIZE)).write(path)


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
