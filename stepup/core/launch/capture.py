# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Capture the output and the resource usage of a step's child process."""

import logging
import os
import subprocess
import threading
import time
from collections.abc import Callable

from ..graph.outcome import ResourceUsage

__all__ = (
    "communicate",
    "decode_output",
    "normalize_newlines",
    "start_drain",
)

logger = logging.getLogger(__name__)


#
# Shared I/O helpers
#


def start_drain(read_all: Callable[[], bytes]) -> Callable[[], bytes]:
    """Start a daemon thread that calls `read_all`, which blocks until EOF.

    Returns a callable that joins the thread and returns the bytes it read.
    Isolates the "read on a background thread, join later" pattern
    shared by `communicate` and `_redirect_os_fds`.
    Both dodge a pipe-full deadlock this way:
    a writer end of the pipe stays open elsewhere (in this process or a child one)
    while the thread drains the corresponding reader end.
    """
    result = b""

    def _run() -> None:
        nonlocal result
        result = read_all()

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()

    def join() -> bytes:
        thread.join()
        return result

    return join


def normalize_newlines(text: str) -> str:
    """Turn every carriage return that is followed by a line feed into nothing.

    Programs on Windows end their lines with both characters.
    All captured output of a step goes through this function, on every platform,
    so that the output does not depend on where or how the step ran.
    """
    return text.replace("\r\n", "\n")


def decode_output(data: bytes) -> str:
    """Decode the captured output of a step, dropping what is not valid UTF-8."""
    return normalize_newlines(data.decode("utf-8", "ignore"))


#
# Generic async subprocess plumbing
#


def communicate(
    proc: subprocess.Popen, stdin_data: bytes | None, wtime_start: float
) -> tuple[bytes, bytes, ResourceUsage]:
    """Communicate with `proc` and return `(stdout, stderr, usage)`.

    Reads stdout and stderr concurrently in threads to avoid a pipe-full deadlock,
    then calls `os.wait4` to reap the child and capture its individual CPU usage.
    Where `os.wait4` does not exist, the child is reaped without measuring its CPU usage,
    which is then reported as zero.
    """
    stdout_join = start_drain(proc.stdout.read) if proc.stdout is not None else None
    stderr_join = start_drain(proc.stderr.read) if proc.stderr is not None else None

    if stdin_data is not None and proc.stdin is not None:
        try:
            proc.stdin.write(stdin_data)
        except BrokenPipeError:
            pass
        finally:
            proc.stdin.close()

    stdout = stdout_join() if stdout_join is not None else b""
    stderr = stderr_join() if stderr_join is not None else b""

    if not hasattr(os, "wait4"):
        proc.wait()
        return stdout, stderr, ResourceUsage(wtime=time.perf_counter() - wtime_start)
    _, status, rusage = os.wait4(proc.pid, 0)
    proc.returncode = os.waitstatus_to_exitcode(status)
    usage = ResourceUsage(
        utime=rusage.ru_utime,
        stime=rusage.ru_stime,
        wtime=time.perf_counter() - wtime_start,
    )
    return stdout, stderr, usage
