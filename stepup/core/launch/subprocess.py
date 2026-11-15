# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Run the command of a step as a subprocess, on Windows in a Job Object of its own."""

import asyncio
import contextlib
import logging
import os
import shlex
import subprocess
import sys
import time

import attrs

from ..exceptions import ConfigError
from ..graph.outcome import ChildOutcome
from ..path import Path
from ..platform.popen import popen_args
from .capture import communicate, decode_output
from .worker import WINDOWS_KILL_EXIT_CODE, Run, SubprocessWorker

if sys.platform == "win32":
    from ..platform.winapi import JobObject

__all__ = (
    "check_executable",
    "run_subprocess",
)

logger = logging.getLogger(__name__)


#
# Executable validation
#


def check_executable(executable: Path, shebang: str | None = None) -> str | None:
    """Check whether the executable can plausibly be run as a step command.

    Parameters
    ----------
    executable
        The (working-directory-resolved) path to the executable to check.
    shebang
        The expected shebang line, if any.

    Returns
    -------
    message
        `None` when the executable is acceptable, otherwise a human-readable error message.
    """
    # Normalize away redundant `.` components,
    # e.g. `cwd / script` doubling up a leading `./` when `cwd` is itself `.`.
    # `path.Path.__truediv__` does not collapse these on its own, unlike `pathlib`.
    executable = executable.normpath()
    # See https://en.wikipedia.org/wiki/Shebang_%28Unix%29
    if not executable.is_file():
        # The executable is probably in the PATH, i.e. not a custom script,
        # so there is nothing to check.
        return None
    if not executable.access(os.X_OK):
        return f"File is not executable: {executable}"
    # Check if the file is binary.
    # https://stackoverflow.com/a/7392391
    with open(executable, "rb") as fh:
        head = fh.read(1024)
    printable_text_chars = bytearray({7, 8, 9, 10, 12, 13, 27} | set(range(0x20, 0x100)) - {0x7F})
    if bool(head.translate(None, printable_text_chars)):
        # This is unlikely to be a script, so not checking the shebang.
        return None
    if shebang is None:
        if head[:3] != b"#!/":
            return f"Script does not start with a shebang: {executable}"
    elif not head.startswith(shebang.encode("utf-8")):
        return f"Script does not start with the expected shebang ({shebang}): {executable}"
    return None


if sys.platform == "win32":
    STEP_ISOLATION = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
else:
    STEP_ISOLATION = {"start_new_session": True}
"""The arguments of `subprocess.Popen` that detach a step from the terminal of the director.

See `_signal_process_group` for POSIX and `_stop_windows_step` for Windows.
"""

_LINGERING_JOBS: list["JobObject"] = []
"""The Job Objects of finished steps that left processes behind, see `_retire_job`."""


def _create_job(proc: subprocess.Popen) -> "JobObject | None":
    """Put a new subprocess in a Job Object of its own, where the platform has them.

    A process that the subprocess starts before it is in the job escapes from the job.
    This is accepted, because the assignment completed about 0.1 ms after `Popen` returned
    when measured on Windows 11 24H2, while a new process needs milliseconds to start.
    Closing the gap takes a process that is created suspended,
    which `subprocess` cannot resume.

    Returns
    -------
    job
        The job that holds the subprocess, or `None` when there is none:
        on other platforms than Windows, or when Windows refuses,
        e.g. because the subprocess has exited already.
    """
    if sys.platform != "win32":
        return None
    try:
        job = JobObject.create()
    except OSError as exc:
        logger.warning("Cannot create a Job Object for subprocess %d: %s", proc.pid, exc)
        return None
    try:
        job.assign(proc.pid)
    except OSError as exc:
        job.close()
        if proc.poll() is None:
            logger.warning("Cannot put subprocess %d in a Job Object: %s", proc.pid, exc)
        return None
    return job


def _close_empty_job(job: "JobObject") -> bool:
    """Close a job if none of its processes is running, and tell whether it was closed."""
    try:
        active = job.query_usage().active
    except OSError:
        active = 0
    if active == 0:
        job.close()
    return job.closed


def _retire_job(job: "JobObject", proc: subprocess.Popen) -> None:
    """Dispose of the job of a step that is no longer the in-flight work of a run.

    A step that is abandoned while it runs, because its task was cancelled,
    is terminated: nothing would stop it later, and nothing reads its output any longer.

    A step that finished may have left processes behind, which then outlive it, as on POSIX.
    Killing them could break another step,
    because build tools start helper processes that later invocations share.
    Their job is kept instead, so that they do end with the director.
    """
    if proc.returncode is None:
        with contextlib.suppress(OSError):
            job.terminate(WINDOWS_KILL_EXIT_CODE)
        job.close()
        return
    _LINGERING_JOBS[:] = [other for other in _LINGERING_JOBS if not _close_empty_job(other)]
    if not _close_empty_job(job):
        _LINGERING_JOBS.append(job)


async def _exec_subprocess(
    cmd: str | list[str], *, shell: bool, env: dict, cwd: Path, stdin_data: bytes | None, run: Run
) -> ChildOutcome:
    """Run `cmd` as a subprocess and return a `ChildOutcome`.

    The process is created synchronously
    so that `run.worker` can be set immediately for interrupts.
    Blocking I/O and the `os.wait4` reap are offloaded to a thread-pool executor
    so the event loop stays responsive.

    Using `subprocess.Popen` (rather than `asyncio.create_subprocess_*`)
    means asyncio's child watcher never registers this PID,
    so the `os.wait4` call captures per-process CPU time without racing against the watcher.
    On Windows, the CPU time is that of the Job Object of the step,
    which covers everything the step started.
    """
    stdin = subprocess.PIPE if stdin_data is not None else subprocess.DEVNULL
    wtime_start = time.perf_counter()
    try:
        proc = subprocess.Popen(
            popen_args(cmd, shell=shell, env=env, cwd=cwd),
            stdin=stdin,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            cwd=cwd,
            **STEP_ISOLATION,
        )
    except ConfigError as exc:
        return ChildOutcome(1, "", f"{exc}\n")
    except OSError as exc:
        return ChildOutcome(1, "", f"Failed to launch command {cmd!r}: {exc}\n")
    job = _create_job(proc)
    run.worker = SubprocessWorker(proc, job, job_i=run.job_i)
    try:
        loop = asyncio.get_running_loop()
        stdout, stderr, usage = await loop.run_in_executor(
            None, communicate, proc, stdin_data, wtime_start
        )
        if job is not None:
            with contextlib.suppress(OSError):
                job_usage = job.query_usage()
                usage = attrs.evolve(usage, utime=job_usage.utime, stime=job_usage.stime)
    finally:
        run.worker = None
        if job is not None:
            _retire_job(job, proc)
    return ChildOutcome(proc.returncode, decode_output(stdout), decode_output(stderr), usage)


async def run_subprocess(
    cmd: str | list[str],
    *,
    shell: bool,
    env: dict,
    cwd: Path,
    run: Run,
    stdin_data: bytes | None = None,
) -> ChildOutcome:
    """Run `cmd` as a subprocess with or without a shell."""
    first_arg = shlex.split(cmd)[0] if isinstance(cmd, str) else cmd[0]
    message = check_executable(cwd / Path(first_arg))
    if message is not None:
        return ChildOutcome(1, "", message + "\n")
    return await _exec_subprocess(
        cmd, shell=shell, env=env, cwd=cwd, stdin_data=stdin_data, run=run
    )
