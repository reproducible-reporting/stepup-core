# SPDX-FileCopyrightText: 2026 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Tests for stepup.core.winapi, which can only run on Windows."""

import os
import subprocess
import sys
import time

import pytest

if sys.platform == "win32":
    from stepup.core.winapi import JobObject, get_peak_working_set, is_process_alive

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
            assert job.query_usage().active == 0
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
        usage = job.query_usage()
        assert usage.active == 0
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
