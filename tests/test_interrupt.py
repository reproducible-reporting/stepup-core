# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""End-to-end tests for aborting a build with a terminal signal.

These run `stepup build` as a real child process, because the behavior under test is about
process groups, signal delivery and exit codes, none of which survive being mocked out.
Ctrl-C is exercised on a pseudo terminal: writing the interrupt character to the pty makes
the line discipline generate a `SIGINT` for the whole foreground process group,
exactly as a key press does.
"""

import contextlib
import os
import pty
import re
import signal
import stat
import subprocess
import sys
import time

import pytest
from path import Path

from stepup.core.enums import ReturnCode

PLAN = """#!/usr/bin/env python3
from stepup.core.api import step

step({command!r}, out=["never.txt"], shell={shell!r})
"""

LEAF = "sleep 300"
"""The long-running process each scenario waits for and expects to be stopped."""

TIMEOUT = 60.0
"""Hard cap for a single interrupt scenario, well above all shutdown grace periods."""

INTERRUPT_CHAR = b"\x03"
"""Ctrl-C, which the terminal line discipline turns into a SIGINT for the foreground group."""

BOOTSTRAP = (
    "import fcntl, os, sys, termios;"
    # Claim the pty (already this process's stdin) as the controlling terminal, then become
    # StepUp. Doing this in a throwaway process that immediately execs, rather than in a
    # `preexec_fn`, keeps arbitrary Python out of the fork of a multi-threaded pytest worker.
    "fcntl.ioctl(0, termios.TIOCSCTTY, 0);"
    "os.execv(sys.executable, [sys.executable, '-m', 'stepup.core', 'build', '--no-progress'])"
)
"""Child-side bootstrap that turns the pty into a controlling terminal and execs StepUp."""


def find_descendants(pid: int) -> dict[int, str]:
    """Return the `pid: command` mapping of all descendant processes of `pid`.

    Descendants rather than children, because a step that is a shell pipeline keeps the
    shell as a wrapper around the process that does the actual work.
    """
    result = subprocess.run(
        ["ps", "-eo", "pid,ppid,args", "--no-headers"], capture_output=True, text=True, check=True
    )
    commands = {}
    children = {}
    for line in result.stdout.splitlines():
        fields = line.split(maxsplit=2)
        if len(fields) == 3:
            commands[int(fields[0])] = fields[2]
            children.setdefault(int(fields[1]), []).append(int(fields[0]))
    descendants = {}
    todo = list(children.get(pid, []))
    while todo:
        current = todo.pop()
        descendants[current] = commands[current]
        todo.extend(children.get(current, []))
    return descendants


def is_alive(pid: int) -> bool:
    """Whether a process still exists."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def wait_gone(pid: int, timeout: float) -> bool:
    """Wait until a process is gone, returning False on timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and is_alive(pid):
        time.sleep(0.1)
    return not is_alive(pid)


def spawn_on_pty(path_tmp: Path) -> tuple[subprocess.Popen, int]:
    """Start `stepup build` as the foreground process group of its own pseudo terminal.

    Returns
    -------
    process
        The terminal user interface subprocess.
    master
        The pty master file descriptor, to write the interrupt character to.
    """
    master, slave = pty.openpty()
    process = subprocess.Popen(
        [sys.executable, "-c", BOOTSTRAP],
        cwd=path_tmp,
        stdin=slave,
        stdout=slave,
        stderr=slave,
        # Make the child a session leader, so it can claim the pty as its controlling
        # terminal and terminal signals are delivered to it (and not to pytest).
        start_new_session=True,
    )
    os.close(slave)
    # Reads must never block: `drain` is called from polling loops that have to keep going
    # even when StepUp has nothing to say.
    os.set_blocking(master, False)
    return process, master


def drain(master: int) -> None:
    """Read whatever the pty has buffered, so the child never blocks on a full buffer."""
    with contextlib.suppress(BlockingIOError, OSError):
        while os.read(master, 65536):
            pass


def wait_for_director(path_tmp: Path, master: int) -> int:
    """Wait until the director logged its pid, and return it."""
    path_log = path_tmp / ".stepup" / "director.log"
    deadline = time.monotonic() + TIMEOUT / 2
    while time.monotonic() < deadline:
        drain(master)
        if path_log.is_file():
            match = re.search(r"^PID (\d+)$", path_log.read_text(), re.MULTILINE)
            if match is not None:
                return int(match.group(1))
        time.sleep(0.1)
    raise AssertionError("The director did not start in time.")


def wait_for_leaf(director_pid: int, master: int) -> int:
    """Wait until the long-running process of the step runs, and return its pid.

    It is located among the descendants of this director, so that a concurrently
    running test cannot be mistaken for it.
    """
    deadline = time.monotonic() + TIMEOUT / 2
    while time.monotonic() < deadline:
        drain(master)
        for pid, command in find_descendants(director_pid).items():
            if command == LEAF:
                return pid
        time.sleep(0.1)
    raise AssertionError(f"The {LEAF!r} process did not start in time.")


def wait_exit(process: subprocess.Popen, master: int, timeout: float) -> int:
    """Wait for the terminal user interface to exit on its own and return its exit code."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        drain(master)
        returncode = process.poll()
        if returncode is not None:
            if returncode < 0:
                signal_name = signal.Signals(-returncode).name
                raise AssertionError(f"stepup was killed by {signal_name} instead of exiting.")
            return returncode
        time.sleep(0.05)
    raise AssertionError(f"stepup did not exit within {timeout} s after the signal.")


@pytest.fixture
def stepup_on_pty(request: pytest.FixtureRequest, path_tmp: Path):
    """Run `stepup build` with one long-running step, and clean up whatever survives it.

    The `(command, shell)` pair of the step can be overridden with indirect parametrization,
    as long as the command still runs `LEAF` somewhere.
    """
    command, shell = getattr(request, "param", (LEAF, False))
    path_plan = path_tmp / "plan.py"
    path_plan.write_text(PLAN.format(command=command, shell=shell))
    path_plan.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    process, master = spawn_on_pty(path_tmp)
    leaf_pid = None
    try:
        director_pid = wait_for_director(path_tmp, master)
        leaf_pid = wait_for_leaf(director_pid, master)
        yield process, master, leaf_pid
    finally:
        for pid in filter(None, [leaf_pid, process.pid]):
            if is_alive(pid):
                with contextlib.suppress(ProcessLookupError):
                    os.kill(pid, signal.SIGKILL)
        process.wait()
        os.close(master)


def test_ctrl_c_aborts_the_build(stepup_on_pty) -> None:
    """A single Ctrl-C stops the running step, and StepUp exits on its own."""
    process, master, leaf_pid = stepup_on_pty
    os.write(master, INTERRUPT_CHAR)
    returncode = wait_exit(process, master, TIMEOUT)
    assert returncode & ReturnCode.INTERRUPTED.value, (
        f"An aborted build must set the INTERRUPTED bit, got returncode {returncode}."
    )
    assert wait_gone(leaf_pid, 5.0), "The running step outlived the build."


def test_ctrl_c_lets_the_director_finish_its_shutdown(stepup_on_pty, path_tmp: Path) -> None:
    """The terminal user interface waits for the director instead of cutting it short."""
    process, master, _ = stepup_on_pty
    os.write(master, INTERRUPT_CHAR)
    wait_exit(process, master, TIMEOUT)
    assert "Traceback" not in (path_tmp / ".stepup" / "director.log").read_text()
    # 'See you!' is the last thing the director reports, after writing its logs.
    assert "See you!" in (path_tmp / ".stepup" / "success.log").read_text()


def test_sigterm_leaves_no_orphans(stepup_on_pty) -> None:
    """SIGTERM reaches only the TUI, so it must be propagated to the director and its steps."""
    process, master, leaf_pid = stepup_on_pty
    process.send_signal(signal.SIGTERM)
    returncode = wait_exit(process, master, TIMEOUT)
    assert returncode & ReturnCode.INTERRUPTED.value
    assert wait_gone(leaf_pid, 5.0), "The running step was orphaned instead of stopped."


@pytest.mark.parametrize("stepup_on_pty", [(f"{LEAF} | cat", True)], indirect=True)
def test_ctrl_c_stops_work_behind_a_shell_wrapper(stepup_on_pty) -> None:
    """A pipeline keeps the shell as a wrapper, so signalling that one process is not enough.

    Steps run in their own session, which is what lets the director signal the whole
    process group and reach the process doing the actual work.
    """
    process, master, leaf_pid = stepup_on_pty
    os.write(master, INTERRUPT_CHAR)
    wait_exit(process, master, TIMEOUT)
    assert wait_gone(leaf_pid, 10.0), "The process behind the shell wrapper kept running."
