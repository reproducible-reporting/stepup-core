# SPDX-FileCopyrightText: 2026 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Tests for stepup.core.winapi, which can only run on Windows."""

import os
import subprocess
import sys
import time

import pytest
from core_common import ConsoleHost

from stepup.core.path import Path

if sys.platform == "win32":
    from stepup.core.winapi import (
        JobObject,
        JobUsage,
        get_peak_working_set,
        is_console,
        is_process_alive,
    )

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows only")


def test_is_process_alive_self() -> None:
    assert is_process_alive(os.getpid())


def test_is_process_alive_exited_child() -> None:
    """An exited child is not alive, although the `Popen` object still holds a handle to it."""
    with subprocess.Popen([sys.executable, "-c", "input()"], stdin=subprocess.PIPE) as proc:
        assert is_process_alive(proc.pid)
        proc.communicate(b"\n")
        assert not is_process_alive(proc.pid)


def test_is_process_alive_exit_code_259() -> None:
    """A process that exits with `STILL_ACTIVE` (259) is not mistaken for a running one."""
    with subprocess.Popen([sys.executable, "-c", "raise SystemExit(259)"]) as proc:
        assert proc.wait() == 259
        assert not is_process_alive(proc.pid)


def test_get_peak_working_set() -> None:
    peak = get_peak_working_set()
    assert 10 * 1024**2 < peak < 100 * 1024**3


#
# Job Objects
#


WAIT_CODE = "import sys; sys.stdin.read()"
"""A Python program that runs until its standard input is closed."""

SPAWN_CODE = (
    "import subprocess, sys; "
    "proc = subprocess.Popen([sys.executable, '-c', 'import sys; sys.stdin.read()'], "
    "stdin=subprocess.PIPE); "
    "print(proc.pid, flush=True); sys.stdin.read()"
)
"""A Python program that starts a child, prints its pid, and waits like `WAIT_CODE`."""

JOB_TIMEOUT = 10.0
"""Seconds to wait for a process to end."""


def _wait_gone(pid: int) -> None:
    deadline = time.monotonic() + JOB_TIMEOUT
    while is_process_alive(pid):
        assert time.monotonic() < deadline, f"Process {pid} did not end in time."
        time.sleep(0.02)


def _wait_idle(job: "JobObject") -> "JobUsage":
    """Poll the job until none of its members is active, and return its usage.

    Windows signals the handle of an exiting process slightly before it updates the accounting
    of the job.
    On a busy machine, `active` may then still count a process for tens of milliseconds
    after `Popen.wait` returned, as observed on Windows 11 24H2 with Python 3.11.
    """
    deadline = time.monotonic() + JOB_TIMEOUT
    usage = job.query_usage()
    while usage.active != 0:
        assert time.monotonic() < deadline, f"The job still has {usage.active} active processes."
        time.sleep(0.02)
        usage = job.query_usage()
    return usage


def test_job_object_terminate_ends_the_whole_tree() -> None:
    job = JobObject.create()
    try:
        with subprocess.Popen(
            [sys.executable, "-c", SPAWN_CODE],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        ) as proc:
            job.assign(proc.pid)
            grandchild_pid = int(proc.stdout.readline())
            assert is_process_alive(grandchild_pid)
            # A virtual environment starts the real interpreter as a child of its `python.exe`.
            assert job.query_usage().active >= 2
            job.terminate(7)
            assert proc.wait(JOB_TIMEOUT) == 7
            _wait_gone(grandchild_pid)
            _wait_idle(job)
    finally:
        job.close()


def test_job_object_close_ends_the_members() -> None:
    job = JobObject.create()
    with subprocess.Popen([sys.executable, "-c", WAIT_CODE], stdin=subprocess.PIPE) as proc:
        job.assign(proc.pid)
        assert not job.closed
        job.close()
        assert job.closed
        proc.wait(JOB_TIMEOUT)
    # Closing again is harmless, using a closed job is a bug.
    job.close()
    with pytest.raises(ValueError, match="closed"):
        job.query_usage()
    with pytest.raises(ValueError, match="closed"):
        job.terminate(1)


def test_job_object_usage_counts_the_cpu_time_of_exited_members() -> None:
    code = (
        "import time; start = time.process_time()\nwhile time.process_time() - start < 0.3: pass\n"
    )
    job = JobObject.create()
    try:
        assert job.query_usage().active == 0
        with subprocess.Popen([sys.executable, "-c", code]) as proc:
            job.assign(proc.pid)
            assert proc.wait(JOB_TIMEOUT) == 0
        usage = _wait_idle(job)
        assert 0.25 < usage.utime + usage.stime < 5.0
    finally:
        job.close()


def test_job_object_assign_exited_process() -> None:
    job = JobObject.create()
    try:
        with subprocess.Popen([sys.executable, "-c", "pass"]) as proc:
            proc.wait(JOB_TIMEOUT)
            with pytest.raises(PermissionError):
                job.assign(proc.pid)
    finally:
        job.close()


def test_is_console_null_device() -> None:
    """The null device is a character device, for which `isatty()` is true, but no console."""
    with open(os.devnull, "rb") as fh:
        assert fh.isatty()
        assert not is_console(fh.fileno())


def test_is_console_file_and_pipe(path_tmp: Path) -> None:
    with open(path_tmp / "file.txt", "wb") as fh:
        assert not is_console(fh.fileno())
    read_fd, write_fd = os.pipe()
    try:
        assert not is_console(read_fd)
        assert not is_console(write_fd)
    finally:
        os.close(read_fd)
        os.close(write_fd)


def test_is_console_closed_fd() -> None:
    read_fd, write_fd = os.pipe()
    os.close(read_fd)
    os.close(write_fd)
    assert not is_console(read_fd)


CONSOLE_READER = """
import sys
from stepup.core.winapi import is_console, read_console_keys

fd = sys.stdin.fileno()
print("console", is_console(fd), is_console(sys.stdout.fileno()), flush=True)
print("idle", ascii(read_console_keys(fd, 0.05)), flush=True)
keys = ""
while "q" not in keys:
    keys += read_console_keys(fd, 0.1)
print("keys", ascii(keys), flush=True)
"""
"""A program that reports what it reads from its console, until the key `q` is pressed."""


def test_read_console_keys(path_tmp: Path) -> None:
    """Keys typed in a real console arrive in order, once for each time they are pressed."""
    host = ConsoleHost.start(
        [sys.executable, "-c", CONSOLE_READER], path_tmp, path_tmp / "reader.out"
    )
    try:
        deadline = time.monotonic() + 10
        while "idle" not in host.output():
            assert time.monotonic() < deadline, "The reader did not start in time."
            time.sleep(0.05)
        host.act("keys ab\u00e9bq")
        assert host.wait(10) == 0
        # The standard input of the program is the console, and its standard output is a file.
        assert host.output().splitlines() == [
            "console True False",
            "idle ''",
            "keys 'ab\\xe9bq'",
        ]
    finally:
        host.close()
