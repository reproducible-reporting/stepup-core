# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Launch a step's command as a child process and report back its `ChildOutcome`.

Also defines `Worker`, the base class for the in-flight work of a `Run` that can be interrupted.
"""

import asyncio
import atexit
import contextlib
import errno
import functools
import importlib
import io
import logging
import multiprocessing
import os
import runpy
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import sysconfig
import threading
import time
from collections.abc import Callable
from importlib.metadata import entry_points
from typing import Any

import attrs

from .api import amend
from .asyncio import wait_for_readable_fd
from .enums import StopLevel
from .exceptions import ConfigError, RunError
from .extapi import get_local_import_paths
from .oscompat import suspend_available
from .outcome import ChildOutcome, ResourceUsage
from .path import Path
from .popen import popen_args
from .step import Step
from .tracebacks import print_step_traceback
from .utils import escape_control_chars

if sys.platform == "win32":
    from .winapi import JobObject
else:
    # Only the forkserver path uses it, and Windows has no forkserver.
    import resource

__all__ = (
    "ForkserverWorker",
    "Run",
    "SubprocessWorker",
    "ThreadWorker",
    "Worker",
    "describe_stop_level",
    "launch_command",
)


logger = logging.getLogger(__name__)


#
# Stop levels
#


STOP_SIGNALS = {
    StopLevel.INTERRUPT: "SIGINT",
    StopLevel.TERMINATE: "SIGTERM",
    StopLevel.KILL: "SIGKILL",
}
"""The POSIX signal that delivers each stop level, by name, because Windows lacks `SIGKILL`."""

WINDOWS_KILL_EXIT_CODE = 1
"""The exit code of the processes of a step that is killed on Windows.

This is the code that `subprocess.Popen.kill` gives to a process there.
"""


def describe_stop_level(level: StopLevel) -> str:
    """Name the mechanism that delivers a stop level to a step on this platform.

    Returns
    -------
    name
        The name of a signal on POSIX, and of a console event or a system call on Windows,
        to tell the user what is being done to the running steps.
    """
    if sys.platform == "win32":
        return "TerminateJobObject" if level == StopLevel.KILL else "CTRL_BREAK_EVENT"
    return STOP_SIGNALS[level]


#
# Worker base class
#


@attrs.define
class Worker:
    """Base class for a subprocess or forkserver child process
    that is the in-flight work of a `Run` and can be stopped by the director.

    Subclasses implement `_describe` and `_signal`;
    `interrupt`, `suspend` and `resume` supply the shared logging and error handling around them.
    A subclass for a process that is not stopped with a signal overrides `_stop` as well.
    A worker with different interrupt semantics
    (e.g. a thread computation, which has no OS process to signal)
    may override `interrupt` instead.
    """

    job_i: int = attrs.field(kw_only=True)
    """The unique id of the job that created this worker, for logging purposes."""

    def interrupt(self, level: StopLevel) -> None:
        """Ask the in-flight work to stop, ignoring a process that has already exited."""
        with contextlib.suppress(ProcessLookupError):
            logger.info(
                "Interrupting %s (job %d) with %s",
                self._describe(),
                self.job_i,
                describe_stop_level(level),
            )
            self._stop(level)

    def suspend(self) -> None:
        """Stop the in-flight work until `resume` is called, where the platform can do that.

        Uses `SIGSTOP` rather than `SIGTSTP`; see `_signal_process_group` for why.
        """
        if not suspend_available():
            return
        with contextlib.suppress(ProcessLookupError):
            logger.info("Suspending %s (job %d)", self._describe(), self.job_i)
            self._signal(signal.SIGSTOP)

    def resume(self) -> None:
        """Continue the work stopped by `suspend`."""
        if not suspend_available():
            return
        with contextlib.suppress(ProcessLookupError):
            logger.info("Resuming %s (job %d)", self._describe(), self.job_i)
            self._signal(signal.SIGCONT)

    def _describe(self) -> str:
        """Return a short human-readable description of the worker, for logging."""
        raise NotImplementedError

    def _stop(self, level: StopLevel) -> None:
        """Deliver a stop level to the underlying process, by default as its POSIX signal."""
        self._signal(getattr(signal, STOP_SIGNALS[level]))

    def _signal(self, sig: int) -> None:
        """Deliver `sig` to the underlying process."""
        raise NotImplementedError


#
# Threaded worker
#


@attrs.define
class ThreadWorker(Worker):
    """A computation running in a dedicated thread.

    Computationally intensive work must be designed to release the GIL.
    A thread cannot be killed, so it must never run code that StepUp does not control,
    such as the command of a step.
    """

    work: Callable[[threading.Event], Any] = attrs.field(kw_only=True)
    """The callable to run in the thread, which can be cancelled by a `threading.Event`."""

    # Internal state

    _cancel_event: threading.Event = attrs.field(init=False, factory=threading.Event)
    """The event that tells the thread to stop at the next opportunity."""

    _loop: asyncio.AbstractEventLoop = attrs.field(init=False)
    """The event loop that created this thread, used to resolve the future."""

    _future: asyncio.Future = attrs.field(init=False)
    """The future that will be resolved with the result of `work` or its exception."""

    _thread: threading.Thread = attrs.field(init=False)
    """The dedicated thread that runs `work`."""

    def __attrs_post_init__(self):
        self._loop = asyncio.get_running_loop()
        self._future = self._loop.create_future()
        self._thread = threading.Thread(target=self._run, daemon=True)

    async def run_in_thread(self) -> Any:
        """Start the thread, wait for its result and return it, re-raising its exception."""
        self._thread.start()
        try:
            return await self._future
        finally:
            self._cancel_event.set()
            # Try to join without waiting, or join in a thread pool if needed.
            self._thread.join(timeout=0)
            if self._thread.is_alive():
                await asyncio.get_running_loop().run_in_executor(None, self._thread.join)

    def interrupt(self, level: StopLevel) -> None:
        """Cancel the computation, whatever the level, because a thread cannot be killed."""
        logger.info("Cancelling background compute thread for job %d", self.job_i)
        self._cancel_event.set()

    def suspend(self) -> None:
        """Ignore the suspend call.

        This thread runs inside the director, which already suspends as a whole,
        together with its threads.
        """

    def resume(self) -> None:
        """Ignore the resume call.

        See `suspend` docstring for details.
        """

    def _run(self) -> None:
        try:
            outcome, error = self.work(self._cancel_event), None
        except BaseException as exc:  # noqa: BLE001
            outcome, error = None, exc
        # The loop is guaranteed to still be running:
        # `run_in_thread` joins this thread in its `finally` clause
        # before the coroutine (and thus the loop) can wind down.
        # Suppressing `RuntimeError` is cheap insurance against abnormal loop teardown.
        with contextlib.suppress(RuntimeError):
            self._loop.call_soon_threadsafe(self._resolve, outcome, error)

    def _resolve(self, outcome: Any, error: BaseException | None) -> None:
        # The future may already be cancelled
        # if the surrounding asyncio task was cancelled independently;
        # `set_result`/`set_exception` would then raise `InvalidStateError`.
        if self._future.done():
            return
        if error is None:
            self._future.set_result(outcome)
        else:
            self._future.set_exception(error)


#
# Worker classes for the child process launched by a step
#


def _signal_process_group(pid: int, sig: int) -> None:
    """Deliver `sig` to the process group led by `pid`, falling back to `pid` itself.

    Every step runs in a session of its own:
    `start_new_session=True` for subprocesses, `os.setsid()` at the top of
    `_forkserver_entry` for forkserver children.
    This makes the pid of the process StepUp started double as its process group id,
    and signalling that group (instead of just the one process) is what reaches the actual
    work when a step is a shell command whose pipeline or `&&`-chain keeps the shell around
    as a wrapper.
    It also detaches the step from the terminal's session, so a Ctrl-C reaches only the
    director, which is then the only thing that decides when to stop a running step.
    The same holds for a Ctrl-Z: the director suspends a step with `SIGSTOP` rather than
    `SIGTSTP` (see `Worker.suspend`).
    Every step's process group is orphaned by construction,
    because its leader's parent (the director) lives in another session,
    and the kernel discards `SIGTSTP` for an orphaned process group whose members have the
    default disposition.

    The fallback to signalling `pid` directly covers the short window in which a forkserver
    child has been forked but has not called `os.setsid` yet, so its group does not exist yet.
    A process that has already exited raises `ProcessLookupError` from both calls,
    which `Worker` methods ignore.
    The fallback is also taken when `os.killpg` raises `PermissionError`,
    because macOS reports `EPERM` instead of `ESRCH` for a group whose only members are zombies,
    which happens when a step has exited but has not been reaped yet.
    A genuine lack of permission then raises `PermissionError` from `os.kill` as well.

    Parameters
    ----------
    pid
        The process id of the step, which is also its process group id.
    sig
        The signal to deliver.
    """
    try:
        os.killpg(pid, sig)
    except (ProcessLookupError, PermissionError):
        os.kill(pid, sig)


def _stop_windows_step(proc: subprocess.Popen, job: "JobObject | None", level: StopLevel) -> None:
    """Deliver a stop level to a step on Windows.

    Every step is started with `CREATE_NEW_PROCESS_GROUP`,
    which is the counterpart of the session that a step gets on POSIX:
    its pid doubles as the id of a process group,
    and the Ctrl-C of the terminal no longer reaches the step and what it starts.
    The step is also put in a Job Object, which holds everything it starts, come what may.

    `INTERRUPT` and `TERMINATE` send `CTRL_BREAK_EVENT` to the process group,
    the only console event that can be sent to a group.
    It ends a process with the status `STATUS_CONTROL_C_EXIT`, unless the process handles it.
    The event travels through the console that the director shares with its steps.
    Without one, there is no gentle way to stop a step, and it is killed straight away.
    `KILL` terminates the Job Object.

    The event must never be sent to a process that does not lead a group:
    Windows 11 24H2 then delivered it to the other processes of the console,
    which include the director.
    An exited group leader is fine: the event still reaches the rest of its group.

    Parameters
    ----------
    proc
        The process that StepUp started for the step, with `CREATE_NEW_PROCESS_GROUP`.
    job
        The Job Object that holds the step, or `None` if it could not be created,
        in which case only `proc` itself can be killed.
    level
        The stop level to deliver.
    """
    if level != StopLevel.KILL:
        try:
            os.kill(proc.pid, signal.CTRL_BREAK_EVENT)
        except OSError as exc:
            logger.info("Cannot send CTRL_BREAK_EVENT to subprocess %d: %s", proc.pid, exc)
        else:
            return
    try:
        if job is None or job.closed:
            proc.kill()
        else:
            job.terminate(WINDOWS_KILL_EXIT_CODE)
    except OSError as exc:
        logger.warning("Cannot kill subprocess %d: %s", proc.pid, exc)


@attrs.define
class SubprocessWorker(Worker):
    """A running subprocess (shell command or direct exec)."""

    proc: subprocess.Popen = attrs.field()
    """The subprocess to stop."""

    job: "JobObject | None" = attrs.field(default=None)
    """The Job Object that holds the subprocess and everything it starts, on Windows."""

    def _describe(self) -> str:
        return f"subprocess {self.proc.pid!r}"

    def _stop(self, level: StopLevel) -> None:
        if sys.platform == "win32":
            _stop_windows_step(self.proc, self.job, level)
        else:
            super()._stop(level)

    def _signal(self, sig: int) -> None:
        _signal_process_group(self.proc.pid, sig)


@attrs.define
class ForkserverWorker(Worker):
    """A running forkserver child process."""

    pid: int = attrs.field()
    """The pid of the forkserver child to signal."""

    def _describe(self) -> str:
        return f"forkserver child {self.pid}"

    def _signal(self, sig: int) -> None:
        _signal_process_group(self.pid, sig)


#
# Mutable state of the current run of a step
#


@attrs.define()
class Run:
    """Mutable state for a single step while it is being executed or skipped."""

    step: Step = attrs.field()
    """The step being executed."""

    job_i: int = attrs.field()
    """Unique id of this run attempt, assigned by `Scheduler` when the job was created.

    Unlike `step.i`, which stays the same across every (re)attempt of a deferred step,
    this id is unique per attempt, so RPC calls can be matched to the attempt that made them.
    """

    description: str = attrs.field(init=False)
    """The escaped form of `step.label`, as shown in the terminal."""

    @description.default
    def _default_description(self) -> str:
        return escape_control_chars(self.step.label)

    outcome: ChildOutcome | None = attrs.field(init=False, default=None)
    """The outcome of the child process, once it has finished."""

    inp_messages: list[str] = attrs.field(init=False, factory=list)
    """Messages related to input validation issues: unexpected changes and deleted inputs."""

    inp_digest: bytes = attrs.field(init=False, default=b"")
    """The input digest, which some steps may use to decide whether cached results are valid."""

    out_missing: list[str] = attrs.field(init=False, factory=list)
    """List of expected output files that were not created."""

    unavailable: set[str] = attrs.field(init=False, factory=set)
    """Dynamic inputs that were genuinely not built yet, if the step was deferred."""

    unfresh: set[str] = attrs.field(init=False, factory=set)
    """Dynamic inputs that failed the freshness check, if the step was deferred."""

    interrupted_defer: bool = attrs.field(init=False, default=False)
    """Set to `True` when the step has reached its defer cap."""

    success: bool = attrs.field(init=False, default=True)
    """Flag indicating whether the step was handled successfully.

    A nonzero returncode sets it `False`,
    but so do independent conditions like missing outputs or unfresh dynamic inputs.
    """

    worker: Worker | None = attrs.field(init=False, default=None)
    """The subprocess, forkserver child, or thread
    currently doing this run's in-flight work, if any.
    """


#
# Executable validation
#


def _check_executable(executable: Path, shebang: str | None = None) -> str | None:
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


#
# Entry point detection
#


@functools.cache
def _get_console_script_entry_points():
    """Return the `console_scripts` entry points of all installed distributions, scanned once.

    Returns
    -------
    eps
        An `EntryPoints` collection, cheap to filter further with `.select(name=...)`.
    """
    return entry_points(group="console_scripts")


def _executable_uses_same_python(path_exec: str) -> bool:
    """Check if an executable script's shebang resolves to the same Python as the current process.

    This detects console_script wrappers installed in PATH-extended locations
    (e.g. additional environment modules loaded on top of the base Python module)
    whose shebang points to the same Python executable as `sys._base_executable`.
    When true, the executable and the forkserver use the same interpreter
    and inherit the same environment variables, so their behavior is equivalent.

    Parameters
    ----------
    path_exec
        Path to the executable script to inspect.

    Returns
    -------
    same
        `True` when the script's shebang resolves to the same interpreter as
        `sys._base_executable`, `False` for non-scripts or a different interpreter.
    """
    base_exec = Path(sys._base_executable).realpath()
    try:
        with open(path_exec, "rb") as f:
            head = f.read(256)
    except OSError:
        return False
    if not head.startswith(b"#!"):
        return False
    try:
        shebang = head.split(b"\n")[0][2:].decode("ascii").strip()
    except UnicodeDecodeError:
        return False
    parts = shebang.split()
    if not parts:
        return False
    # Handle both `#!/path/to/python` and `#!/usr/bin/env python3` forms.
    if Path(parts[0]).name == "env" and len(parts) > 1:
        python_on_path = shutil.which(parts[1])
        if python_on_path is None:
            return False
        shebang_python = Path(python_on_path).realpath()
    else:
        shebang_python = Path(parts[0]).realpath()
    return shebang_python == base_exec


def _executable_compatible_with_current_python(which_path: str) -> bool:
    """Check if an executable is usable with the current Python environment.

    Fast path: the executable lives inside the current environment's directory for scripts
    (`bin`, or `Scripts` on Windows), or that of the base environment it was created from.
    Slow path: the executable's shebang resolves to the same Python interpreter,
    covering packages installed via PYTHONPATH-extending environment modules.

    Parameters
    ----------
    which_path
        Path to the executable to check, e.g. as returned by `shutil.which`.

    Returns
    -------
    compatible
        `True` when the executable is inside the current Python environment,
        or its shebang resolves to the same interpreter as `sys._base_executable`.
        `False` otherwise.
    """
    resolved = Path(which_path).realpath()
    # Only the name of the directory is taken from `sysconfig`,
    # whose full path may be one that a distribution reserves for locally installed packages.
    scripts = Path(sysconfig.get_path("scripts")).name
    prefixes = {sys.prefix, sys.exec_prefix, sys.base_prefix, sys.base_exec_prefix}
    env_bins = {(Path(prefix) / scripts).realpath() for prefix in prefixes}
    path_ok = any(resolved.startswith(d / "") or resolved == d for d in env_bins)
    return path_ok or _executable_uses_same_python(which_path)


_PYTHON_ENTRYPOINT_CACHE: dict[str, tuple[str | None, str]] = {}


def _detect_python_entrypoint(cmd: str) -> tuple[str | None, str]:
    """Detect if `cmd` is a console_script compatible with the current Python environment.

    Parameters
    ----------
    cmd
        The bare command name (no path separators) to look up.

    Returns
    -------
    ep_value
        The entry point value string (e.g. `"pytest:main"`) when `cmd` is a console_script
        importable in and compatible with the current Python environment, or `None` otherwise.
    warning
        A message explaining why a console_script was rejected,
        or an empty string when there is nothing to report.
        The message is repeated for every detection of the same `cmd`,
        so that each affected step reports it,
        independently of the order in which steps are scheduled.

    Raises
    ------
    RunError
        When `cmd` is registered as a console_script but cannot be found on `PATH`,
        which indicates a broken installation.
    """
    if cmd in _PYTHON_ENTRYPOINT_CACHE:
        return _PYTHON_ENTRYPOINT_CACHE[cmd]
    ep_value = None
    warning = ""
    eps = list(_get_console_script_entry_points().select(name=cmd))
    if len(eps) > 0:
        which_path = shutil.which(cmd)
        if which_path is None:
            # Deliberately not cached,
            # so a repaired installation is picked up without restarting the director.
            raise RunError(
                f"Command '{cmd}' is registered as a Python console_script entry point "
                "but was not found on PATH. The installation may be broken."
            )
        if _executable_compatible_with_current_python(which_path):
            ep_value = eps[0].value
        else:
            warning = (
                f"WARNING: Entry point '{cmd}' ('{which_path}') is outside the current"
                f" Python environment ({sys.prefix}). Running it as a subprocess.\n"
            )
    _PYTHON_ENTRYPOINT_CACHE[cmd] = (ep_value, warning)
    return ep_value, warning


#
# Shared I/O helpers
#


def _start_drain(read_all: Callable[[], bytes]) -> Callable[[], bytes]:
    """Start a daemon thread that calls `read_all`, which blocks until EOF.

    Returns a callable that joins the thread and returns the bytes it read.
    Isolates the "read on a background thread, join later" pattern
    shared by `_communicate` and `_redirect_os_fds`.
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


def _normalize_newlines(text: str) -> str:
    """Turn every carriage return that is followed by a line feed into nothing.

    Programs on Windows end their lines with both characters.
    All captured output of a step goes through this function, on every platform,
    so that the output does not depend on where or how the step ran.
    """
    return text.replace("\r\n", "\n")


def _decode(data: bytes) -> str:
    """Decode the captured output of a step, dropping what is not valid UTF-8."""
    return _normalize_newlines(data.decode("utf-8", "ignore"))


#
# Generic async subprocess plumbing
#


def _communicate(
    proc: subprocess.Popen, stdin_data: bytes | None, wtime_start: float
) -> tuple[bytes, bytes, ResourceUsage]:
    """Communicate with `proc` and return `(stdout, stderr, usage)`.

    Reads stdout and stderr concurrently in threads to avoid a pipe-full deadlock,
    then calls `os.wait4` to reap the child and capture its individual CPU usage.
    Where `os.wait4` does not exist, the child is reaped without measuring its CPU usage,
    which is then reported as zero.
    """
    stdout_join = _start_drain(proc.stdout.read) if proc.stdout is not None else None
    stderr_join = _start_drain(proc.stderr.read) if proc.stderr is not None else None

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
            None, _communicate, proc, stdin_data, wtime_start
        )
        if job is not None:
            with contextlib.suppress(OSError):
                job_usage = job.query_usage()
                usage = attrs.evolve(usage, utime=job_usage.utime, stime=job_usage.stime)
    finally:
        run.worker = None
        if job is not None:
            _retire_job(job, proc)
    return ChildOutcome(proc.returncode, _decode(stdout), _decode(stderr), usage)


async def _run_subprocess(
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
    message = _check_executable(cwd / Path(first_arg))
    if message is not None:
        return ChildOutcome(1, "", message + "\n")
    return await _exec_subprocess(
        cmd, shell=shell, env=env, cwd=cwd, stdin_data=stdin_data, run=run
    )


#
# Python-specialized execution paths
#


async def _recv_conn(conn):
    """Asynchronously receive one object from a multiprocessing connection."""
    await wait_for_readable_fd(conn.fileno())
    return conn.recv()


async def _wait_proc(proc):
    """Asynchronously wait for a multiprocessing process to exit, then reap it."""
    await wait_for_readable_fd(proc.sentinel)
    proc.join()


def _lost_child_outcome(exitcode: int | None) -> ChildOutcome:
    """Describe a forkserver child that died before sending its `ChildOutcome`.

    Whatever the child had written to stdout or stderr is lost with it:
    it is buffered in the child and only travels back over the pipe as part of the outcome.

    Parameters
    ----------
    exitcode
        The exit code of the joined child process,
        negative when it was terminated by a signal.

    Returns
    -------
    outcome
        A failed outcome explaining how the child died.
    """
    if exitcode is not None and exitcode < 0:
        try:
            reason = signal.Signals(-exitcode).name
        except ValueError:
            reason = f"signal {-exitcode}"
        returncode = exitcode
    else:
        # A child that exits without sending anything (e.g. a step calling `os._exit`)
        # can never get here with exitcode 0, but a failed run must not report success.
        reason = f"exit code {exitcode}"
        returncode = exitcode if exitcode else 1
    return ChildOutcome(
        returncode, "", f"The Python step died ({reason}) before sending back its result.\n"
    )


async def _exec_in_forkserver(
    mp_ctx: multiprocessing.context.BaseContext,
    target,
    args: tuple,
    run: Run,
) -> ChildOutcome:
    """Run `target(*args, conn)` in a forkserver child and return the `ChildOutcome` it sends back.

    The child's pid is recorded on `run` so a running step can be interrupted.
    A child that dies without sending an outcome
    (typically because the build was aborted and it was killed with `SIGKILL`)
    yields a failed outcome describing how it died, just like a subprocess killed by a signal,
    instead of an `EOFError` escaping as an internal director error.
    """
    parent_conn, child_conn = mp_ctx.Pipe(duplex=False)
    proc = mp_ctx.Process(target=target, args=(*args, child_conn))
    proc.start()
    child_conn.close()
    run.worker = ForkserverWorker(proc.pid, job_i=run.job_i)
    try:
        outcome = await _recv_conn(parent_conn)
    except (EOFError, OSError):
        # EOFError: the child died with the pipe still empty.
        # OSError (e.g. ConnectionResetError): it died halfway through sending.
        outcome = None
    finally:
        await _wait_proc(proc)
        parent_conn.close()
        run.worker = None
    return _lost_child_outcome(proc.exitcode) if outcome is None else outcome


@contextlib.contextmanager
def _redirect_os_fds(stdout_buf: io.StringIO, stderr_buf: io.StringIO):
    """Capture OS-level fd 1/2 output (subprocesses, C extensions)
    that a `sys.stdout` / `sys.stderr` `StringIO` redirect does not see.

    Redirects fds 1 and 2 to pipes drained on background threads
    for the duration of the `with` block,
    then restores the original fds
    and appends whatever the drain threads read to `stdout_buf` / `stderr_buf`.
    Restoring (rather than closing) the original fds is what signals EOF to the drain threads,
    which is what unblocks their join.
    """
    saved_out_fd = os.dup(1)
    saved_err_fd = os.dup(2)
    r_out, w_out = os.pipe()
    r_err, w_err = os.pipe()
    os.dup2(w_out, 1)
    os.close(w_out)
    os.dup2(w_err, 2)
    os.close(w_err)

    def _read_fd_to_eof(fd: int) -> bytes:
        # Blocks until EOF, i.e. until every writer of this pipe end has closed it.
        # Same semantics as `_communicate`:
        # a step that leaves a daemon holding the inherited fd open
        # will keep this thread (and the join that waits for it) waiting.
        with os.fdopen(fd, "rb") as f:
            return f.read()

    join_stdout = _start_drain(functools.partial(_read_fd_to_eof, r_out))
    join_stderr = _start_drain(functools.partial(_read_fd_to_eof, r_err))
    try:
        yield
    finally:
        os.dup2(saved_out_fd, 1)
        os.close(saved_out_fd)
        os.dup2(saved_err_fd, 2)
        os.close(saved_err_fd)
        # Decode tolerantly: subprocesses may emit invalid UTF-8.
        stdout_buf.write(_decode(join_stdout()))
        stderr_buf.write(_decode(join_stderr()))


def _forkserver_entry(
    cmd: str,
    args: list[str],
    env_snapshot: dict[str, str],
    workdir: str,
    ep_value: str | None,
    result_conn,
) -> None:
    """Run a Python script or console_script entry point in a forkserver child.

    This function runs in a forked child process
    and sends a `ChildOutcome` back via `result_conn`.
    When `ep_value` is `None`, `cmd` is a Python script path run via `runpy.run_path`,
    with local imports auto-detected and registered as dynamic inputs.
    When `ep_value` is a `module:attr` string,
    the corresponding console_script function is imported and called directly
    without import tracking.
    """
    # Put the step in its own session; see `_signal_process_group`.
    # This cannot fail: a freshly forked child is never a process group leader.
    os.setsid()
    # Note that the time needed to start/stop the forkserver child is not counted,
    # which is a minor accepted discrepancy with the subprocess path.
    wtime_start = time.perf_counter()
    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()
    returncode = 0
    ru_self_start = resource.getrusage(resource.RUSAGE_SELF)
    ru_children_start = resource.getrusage(resource.RUSAGE_CHILDREN)
    # The inner try/except must run inside the `with` block,
    # so that the fd restore and append (in the `with` block's teardown)
    # happen after the traceback (if any) has already been written to `stderr_buf`, not before.
    with _redirect_os_fds(stdout_buf, stderr_buf):
        try:
            os.environ.clear()
            os.environ.update(env_snapshot)
            os.chdir(workdir)
            sys.stdout = stdout_buf
            sys.stderr = stderr_buf
            sys.argv = [cmd, *args]
            try:
                if ep_value is None:
                    script_dir = str(Path(cmd).realpath().parent)
                    sys.path[0] = script_dir
                    runpy.run_path(cmd, run_name="__main__")
                else:
                    module_name, attr_name = ep_value.split(":", 1)
                    func = getattr(importlib.import_module(module_name), attr_name)
                    func()
            except SystemExit as exc:
                returncode = (
                    exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
                )
            finally:
                # Run `atexit` handlers before sending the result,
                # so that any `amend()` calls from `atexit` handlers
                # are processed while the step is still RUNNING.
                # There is no public API for this in CPython;
                # `_run_exitfuncs` is a stable private implementation detail,
                # present in every CPython release from 2.0 to 3.14 (last checked 2026-08).
                with contextlib.suppress(AttributeError):
                    atexit._run_exitfuncs()
                if ep_value is None:
                    # This is the first call that connects to the director in this child.
                    # The connection must not be opened before the fork,
                    # which is why `get_rpc_client()` creates it lazily.
                    amend(inp=get_local_import_paths(script_path=Path(cmd)))
        except BaseException as exc:  # noqa: BLE001
            # All exceptions must be caught here,
            # to be able to send the corresponding output and return code
            # back to the director process.
            # Otherwise, the parent process would just see a connection error.
            # This path catches the exception itself, so `sys.excepthook` never runs
            # and the traceback shortening must be requested explicitly.
            print_step_traceback(exc, stderr_buf)
            returncode = 1
        finally:
            # Snapshot in a `finally`:
            # a step failing with an uncaught exception skips the tail of the `try` body,
            # which would otherwise leave the usage at zero
            # even though the step did consume CPU time.
            ru_self_end = resource.getrusage(resource.RUSAGE_SELF)
            ru_children_end = resource.getrusage(resource.RUSAGE_CHILDREN)
    wtime_end = time.perf_counter()
    usage = ResourceUsage.from_diff(
        ru_self_start, ru_self_end, ru_children_start, ru_children_end, wtime_start, wtime_end
    )
    result_conn.send(
        ChildOutcome(
            returncode,
            _normalize_newlines(stdout_buf.getvalue()),
            _normalize_newlines(stderr_buf.getvalue()),
            usage,
        )
    )


PYCODE_WRAPPER = """\
import os
import sys
import runpy
from stepup.core.path import Path
from stepup.core.api import amend
from stepup.core.extapi import get_local_import_paths
sys.argv = {argv}
try:
    runpy.run_path({script}, run_name="__main__")
finally:
    amend(inp=get_local_import_paths(script_path=Path({script})))
"""


async def _run_python_script(
    script: str,
    args: list[str],
    env: dict,
    cwd: Path,
    mp_ctx: multiprocessing.context.BaseContext | None,
    run: Run,
) -> ChildOutcome:
    """Run a Python script, amending its local imports as inputs."""
    message = _check_executable(cwd / Path(script), shebang="#!/usr/bin/env python3")
    if message is not None:
        return ChildOutcome(1, "", message + "\n")
    if mp_ctx is not None:
        return await _exec_in_forkserver(
            mp_ctx, _forkserver_entry, (script, args, env, str(cwd), None), run
        )
    wrapper = PYCODE_WRAPPER.format(argv=repr([script, *args]), script=repr(script))
    return await _run_subprocess(
        [sys.executable, "-"], shell=False, env=env, cwd=cwd, run=run, stdin_data=wrapper.encode()
    )


async def _run_python_entrypoint(
    cmd: str,
    args: list[str],
    ep_value: str,
    env: dict,
    cwd: Path,
    mp_ctx: multiprocessing.context.BaseContext | None,
    run: Run,
) -> ChildOutcome:
    """Run a Python console_script entry point, using the forkserver when available."""
    if mp_ctx is not None:
        return await _exec_in_forkserver(
            mp_ctx, _forkserver_entry, (cmd, args, env, str(cwd), ep_value), run
        )
    return await _run_subprocess([cmd, *args], shell=False, env=env, cwd=cwd, run=run)


#
# Built-in copy
#


COPY_CHUNK_SIZE = 1 << 18
"""The number of bytes copied between two checks of the cancel event."""


def _is_builtin_copy(parts: list[str]) -> bool:
    """Whether the words of a command are exactly `cp -p SRC DST`, without further options."""
    return (
        len(parts) == 4
        and parts[:2] == ["cp", "-p"]
        and not any(part.startswith("-") for part in parts[2:])
    )


def _copy_file(src: Path, dst: Path, cancel_event: threading.Event) -> bool:
    """Copy a file together with its permission bits, timestamps and owner, as `cp -p` does.

    Like `cp`, this follows symbolic links, copies into `dst` when that is a directory,
    overwrites an existing destination in place,
    and silently keeps the owner of the copy when it may not be changed.
    Unlike `shutil.copystat`, it leaves extended attributes alone,
    which include the security context of SELinux.

    Parameters
    ----------
    src
        The file to copy.
    dst
        The destination file, or an existing directory in which the copy gets the name of `src`.
    cancel_event
        The event that is checked between chunks of `COPY_CHUNK_SIZE` bytes.

    Returns
    -------
    completed
        `False` when `cancel_event` was set before the whole file was copied,
        which leaves an incomplete destination behind.

    Raises
    ------
    OSError
        When the file cannot be copied.
    """
    if src.is_dir():
        raise IsADirectoryError(errno.EISDIR, "The source is a directory", str(src))
    if dst.is_dir():
        dst = dst / src.name
    with open(src, "rb", buffering=0) as fsrc:
        src_stat = os.fstat(fsrc.fileno())
        # Opening the destination truncates it, which would destroy the source.
        if dst.exists() and dst.samefile(src):
            raise OSError(
                errno.EINVAL, "The source and the destination are the same file", str(src)
            )
        # A new destination is private until it gets the permission bits of the source.
        opener = functools.partial(os.open, mode=stat.S_IRUSR | stat.S_IWUSR)
        with open(dst, "wb", opener=opener) as fdst:
            buf = bytearray(COPY_CHUNK_SIZE)
            view = memoryview(buf)
            # As in `compute_file_digest`, only `nread == 0` means the end of the file.
            while True:
                if cancel_event.is_set():
                    return False
                nread = fsrc.readinto(buf)
                if nread == 0:
                    break
                fdst.write(view[:nread])
    os.utime(dst, ns=(src_stat.st_atime_ns, src_stat.st_mtime_ns))
    if hasattr(os, "chown"):
        # Changing the owner takes privileges, changing the group only membership of it.
        try:
            os.chown(dst, src_stat.st_uid, src_stat.st_gid)
        except OSError:
            with contextlib.suppress(OSError):
                os.chown(dst, -1, src_stat.st_gid)
    # The permission bits come last, because changing the owner may clear some of them.
    os.chmod(dst, stat.S_IMODE(src_stat.st_mode))
    return True


def _builtin_copy(src: Path, dst: Path, cancel_event: threading.Event) -> ChildOutcome:
    """Copy a file as `_copy_file` does, and describe the result as that of a command.

    The CPU time of the copy is part of that of the director, and is not reported here.
    """
    wtime_start = time.perf_counter()
    try:
        completed = _copy_file(src, dst, cancel_event)
    except (OSError, ValueError) as exc:
        # A path that no file system function accepts raises `ValueError`.
        returncode, stderr = 1, f"Failed to copy {src} to {dst}: {exc}\n"
    else:
        returncode, stderr = (0, "") if completed else (1, f"Interrupted the copy to {dst}.\n")
    usage = ResourceUsage(wtime=time.perf_counter() - wtime_start)
    return ChildOutcome(returncode, "", stderr, usage)


async def _run_builtin_copy(src: str, dst: str, cwd: Path, run: Run) -> ChildOutcome:
    """Copy a file in a thread of the director, relative to the working directory of the step."""
    worker = ThreadWorker(
        work=functools.partial(_builtin_copy, cwd / src, cwd / dst), job_i=run.job_i
    )
    run.worker = worker
    try:
        return await worker.run_in_thread()
    finally:
        run.worker = None


#
# Command dispatching
#


async def launch_command(
    command: str,
    *,
    shell: bool,
    env: dict,
    cwd: Path,
    mp_ctx: multiprocessing.context.BaseContext | None,
    run: Run,
) -> ChildOutcome:
    """Launch a step's command and return its `ChildOutcome`.

    Dispatches to a shell, a Python script (`*.py`), a Python console_script entry point,
    or a plain (non-shell) exec.
    Python scripts and entry points run in a forkserver child when `mp_ctx` is not `None`,
    and as a plain subprocess otherwise.
    The one command that starts no process at all is `cp -p SRC DST` without a shell:
    the director copies the file itself,
    because starting a process for it is slow on platforms without a forkserver,
    and because `cp` does not exist on Windows.

    Parameters
    ----------
    command
        The command line to launch, as it would be typed in a shell.
    shell
        Whether to run `command` through a shell instead of splitting and exec'ing it directly.
    env
        The environment variables for the child process.
    cwd
        The working directory for the child process.
    mp_ctx
        The forkserver multiprocessing context, or `None` to use plain subprocesses.
    run
        The `Run` whose `worker` attribute is set to the launched child while it is in flight.

    Returns
    -------
    outcome
        The `ChildOutcome` of the launched command.

    Raises
    ------
    ValueError
        When `command` is empty, which is a programming error in the director.
    """
    parts = shlex.split(command)
    if not parts:
        raise ValueError(f"Empty command: {command!r}")
    if not shell and _is_builtin_copy(parts):
        return await _run_builtin_copy(parts[2], parts[3], cwd, run)
    if shell:
        return await _run_subprocess(command, shell=True, env=env, cwd=cwd, run=run)
    if parts[0].endswith(".py"):
        return await _run_python_script(parts[0], parts[1:], env, cwd, mp_ctx, run)
    try:
        ep_value, warning = _detect_python_entrypoint(parts[0])
    except RunError as exc:
        return ChildOutcome(1, "", str(exc) + "\n")
    if ep_value is None:
        outcome = await _run_subprocess(parts, shell=False, env=env, cwd=cwd, run=run)
    else:
        outcome = await _run_python_entrypoint(parts[0], parts[1:], ep_value, env, cwd, mp_ctx, run)
    if len(warning) > 0:
        outcome = attrs.evolve(outcome, stderr=warning + outcome.stderr)
    return outcome
