# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Launch a step's command as a child process and report back its `ChildOutcome`.

Also defines `Worker`, the base class for the in-flight work of a `Run` that can be
interrupted.
"""

import asyncio
import atexit
import contextlib
import functools
import importlib
import io
import logging
import multiprocessing
import os
import resource
import runpy
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
import traceback
from collections.abc import Callable
from importlib.metadata import entry_points
from typing import Any

import attrs
from path import Path

from .asyncio import await_fd_readable
from .exceptions import RunError
from .extapi import get_local_import_paths
from .outcome import ChildOutcome, ResourceUsage
from .step import Step
from .utils import escape_command_display

__all__ = (
    "ForkserverWorker",
    "Run",
    "SubprocessWorker",
    "ThreadWorker",
    "Worker",
    "launch_command",
)


logger = logging.getLogger(__name__)


#
# Worker base class
#


@attrs.define
class Worker:
    """Base class for a subprocess or forkserver child process that is the in-flight work
    of a `Run` and can be interrupted with a signal.

    Subclasses implement `_describe` and `_signal`; `interrupt` supplies the shared logging
    and error handling around them. A worker with different interrupt semantics (e.g. a
    thread computation, which has no OS process to signal) may override `interrupt` instead.
    """

    job_i: int = attrs.field(kw_only=True)
    """The unique id of the job that created this worker, for logging purposes."""

    def interrupt(self, sig: int) -> None:
        """Interrupt the in-flight work, ignoring a process that has already exited."""
        with contextlib.suppress(ProcessLookupError):
            logger.info(
                "Interrupting %s (job %d) with signal %d", self._describe(), self.job_i, sig
            )
            self._signal(sig)

    def _describe(self) -> str:
        """A short human-readable description of the worker, for logging."""
        raise NotImplementedError

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
    This is primarily used for hashing now.
    It may later also be used to offload other CPU-bound work from the director.
    Threads should never be used for client-specific commands.
    """

    work: Callable[[threading.Event], Any] = attrs.field(kw_only=True)
    """The callable to run in the thread, which can be canceled by a `threading.Event`."""

    # Internal state

    _cancel_event: threading.Event = attrs.field(init=False, factory=threading.Event)
    """The event that tells the hash thread to stop at the next opportunity."""

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
        """Wait for the thread to finish and return its result, re-raising its exception."""
        self._thread.start()
        try:
            return await self._future
        finally:
            self._cancel_event.set()
            # Try to join without wait, or join in a threadpool if needed.
            self._thread.join(timeout=0)
            if self._thread.is_alive():
                await asyncio.get_running_loop().run_in_executor(None, self._thread.join)

    def interrupt(self, sig: int) -> None:
        logger.info("Cancelling background compute thread for job %d", self.job_i)
        self._cancel_event.set()

    def _run(self) -> None:
        try:
            outcome, error = self.work(self._cancel_event), None
        except BaseException as exc:  # noqa: BLE001
            outcome, error = None, exc
        # The loop is guaranteed to still be running: run_in_thread joins this
        # thread in its finally clause before the coroutine (and thus the loop) can
        # wind down. suppress is cheap insurance against abnormal loop teardown.
        with contextlib.suppress(RuntimeError):
            self._loop.call_soon_threadsafe(self._resolve, outcome, error)

    def _resolve(self, outcome: Any, error: BaseException | None) -> None:
        # The future may already be cancelled if the surrounding asyncio task was
        # cancelled independently; set_result/set_exception would then raise
        # InvalidStateError.
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

    Every step runs in a session of its own, so the pid of the process StepUp started is
    also its process group id. Signalling the group (instead of just that one process)
    is what reaches the actual work when a step is a shell command whose pipeline or
    `&&`-chain keeps the shell around as a wrapper.

    The fallback covers the short window in which a forkserver child has been forked but
    has not called `os.setsid` yet, so that its group does not exist.
    A process that has already exited raises `ProcessLookupError` from both calls,
    which `Worker.interrupt` ignores.

    Parameters
    ----------
    pid
        The process id of the step, which is also its process group id.
    sig
        The signal to deliver.
    """
    try:
        os.killpg(pid, sig)
    except ProcessLookupError:
        os.kill(pid, sig)


@attrs.define
class SubprocessWorker(Worker):
    """A running subprocess (shell command or direct exec)."""

    proc: subprocess.Popen = attrs.field()
    """The subprocess to signal."""

    def _describe(self) -> str:
        return f"subprocess {self.proc.pid!r}"

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


@attrs.define(eq=False)
class Run:
    """Mutable state for a single step while it is being executed or skipped.

    Instances use identity-based equality and hashing so they can be tracked in a set
    of currently running steps.
    """

    step: Step = attrs.field()
    """The step being executed."""

    job_i: int = attrs.field()
    """Unique id of this run attempt, assigned by `Scheduler` when the job was created.

    Unlike `step.i`, which stays the same across every (re)attempt of a deferred step, this
    id is unique per attempt, so RPC calls can be matched to the attempt that made them.
    """

    description: str = attrs.field(init=False)
    """The escaped form of `step.label`, as shown in the terminal."""

    @description.default
    def _default_description(self) -> str:
        return escape_command_display(self.step.label)

    outcome: ChildOutcome | None = attrs.field(init=False, default=None)

    inp_messages: list[str] = attrs.field(init=False, factory=list)
    """Messages related to input validation issues: unexpected changes and deleted inputs."""

    inp_digest: bytes = attrs.field(init=False, default=b"")
    """The input digest, which can be useful for some steps."""

    out_missing: list[str] = attrs.field(init=False, factory=list)
    """List of expected output files that were not created."""

    unavailable: set[str] = attrs.field(init=False, factory=set)
    """Dynamic inputs that were genuinely not built yet, if the step was deferred."""

    unfresh: set[str] = attrs.field(init=False, factory=set)
    """Dynamic inputs that failed the freshness check, if the step was deferred."""

    interrupted_defer: bool = attrs.field(init=False, default=False)
    """Set to True when the step has reached its defer cap."""

    detached: bool = attrs.field(init=False, default=False)
    """Set to True when `Step.completed()` found this step had already been detached by
    its creator (see `Step.detach()`) when it finished, regardless of success or failure.
    """

    success: bool = attrs.field(init=False, default=True)
    """Flag indicating whether the step was handled successfully.

    A nonzero returncode sets it False,
    but so do independent conditions like missing outputs or unfresh dynamic inputs.
    """

    worker: Worker | None = attrs.field(init=False, default=None)
    """The subprocess, forkserver child, or hash thread currently doing this run's
    in-flight work, if any.
    """


#
# Executable validation
#


def _check_executable(executable: Path, shebang: str | None = None) -> str | None:
    """Check if the executable looks fine.

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
    # See https://en.wikipedia.org/wiki/Shebang_%28Unix%29
    if not executable.is_file():
        # The executable is probably in the PATH,
        # i.e. not a custom script, so not checking
        return None
    # Check if the file is executable
    if not executable.access(os.X_OK):
        # This is not a script, so not checking the shebang.
        return f"File is not executable: {executable}"
    # Check if the file is binary.
    # https://stackoverflow.com/a/7392391
    with open(executable, "rb") as fh:
        head = fh.read(1024)
    printable_text_chars = bytearray({7, 8, 9, 10, 12, 13, 27} | set(range(0x20, 0x100)) - {0x7F})
    # Check if the file is binary by translating non-text characters
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
    """The `console_scripts` entry points of all installed distributions, scanned once.

    Returns
    -------
    eps
        An `EntryPoints` collection, cheap to filter further with `.select(name=...)`.
    """
    return entry_points(group="console_scripts")


def _executable_uses_same_python(path_exec: str) -> bool:
    """Check if an executable script's shebang resolves to the same Python as the current process.

    This detects console_script wrappers installed in PATH-extended locations
    (e.g., additional environment modules loaded on top of the base Python module)
    whose shebang points to the same Python executable as `sys._base_executable`.
    When true, the executable and the forkserver use the same interpreter and inherit
    the same environment variables, so their behavior is equivalent.

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

    Fast path: the executable lives inside the current environment's `bin` directory,
    or that of the base environment it was created from.
    Slow path: the executable's shebang resolves to the same Python interpreter,
    covering packages installed via PYTHONPATH-extending environment modules.

    Parameters
    ----------
    which_path
        Path to the executable to check, as e.g. returned by `shutil.which`.

    Returns
    -------
    compatible
        `True` when the executable is inside the current Python environment,
        or its shebang resolves to the same interpreter as `sys._base_executable`.
        `False` otherwise.
    """
    resolved = Path(which_path).realpath()
    env_bins = {(Path(sys.prefix) / "bin").realpath(), (Path(sys.exec_prefix) / "bin").realpath()}
    if sys.prefix != sys.base_prefix:
        env_bins.add((Path(sys.base_prefix) / "bin").realpath())
        env_bins.add((Path(sys.base_exec_prefix) / "bin").realpath())
    path_ok = any(resolved.startswith(d / "") or resolved == d for d in env_bins)
    return path_ok or _executable_uses_same_python(which_path)


@functools.cache
def _detect_python_entrypoint(cmd: str) -> str | None:
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

    Raises
    ------
    ValueError
        When `cmd` is registered as a console_script but cannot be found on `PATH`,
        which indicates a broken installation.
    """
    eps = list(_get_console_script_entry_points().select(name=cmd))
    if not eps:
        return None
    ep_value = eps[0].value
    which_path = shutil.which(cmd)
    if which_path is None:
        raise RunError(
            f"Command '{cmd}' is registered as a Python console_script entry point "
            "but was not found on PATH. The installation may be broken."
        )
    if not _executable_compatible_with_current_python(which_path):
        print(
            f"WARNING: Command '{cmd}' is a Python entry point but its executable"
            f" ('{which_path}') is not in the current Python environment ({sys.prefix})."
            " Falling back to direct subprocess execution.",
            file=sys.stderr,
        )
        return None
    return ep_value


#
# Shared I/O helpers
#


def _start_drain(read_all: Callable[[], bytes]) -> Callable[[], bytes]:
    """Start a daemon thread that calls `read_all`, which blocks until EOF.

    Returns a callable that joins the thread and returns the bytes it read. Isolates the
    "read on a background thread, join later" pattern shared by `_communicate_wait4` and
    `_redirect_os_fds`. Both dodge a pipe-full deadlock this way: a writer end of the pipe
    stays open elsewhere (in this process or a child one) while the thread drains the
    corresponding reader end.
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


def _decode(data: bytes) -> str:
    return data.decode("utf-8", "ignore")


#
# Generic async subprocess plumbing
#


def _communicate_wait4(
    proc: subprocess.Popen, stdin_data: bytes | None, wtime_start: float
) -> tuple[bytes, bytes, ResourceUsage]:
    """Communicate with `proc` and return `(stdout, stderr, usage)`.

    Reads stdout and stderr concurrently in threads to avoid pipe-full deadlock,
    then calls `os.wait4` to reap the child and capture its individual CPU usage.
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

    _, status, rusage = os.wait4(proc.pid, 0)
    proc.returncode = os.waitstatus_to_exitcode(status)
    usage = ResourceUsage(
        utime=rusage.ru_utime,
        stime=rusage.ru_stime,
        wtime=time.perf_counter() - wtime_start,
    )
    return stdout, stderr, usage


async def _exec_subprocess(
    cmd, *, shell: bool, env: dict, cwd: Path, stdin_data: bytes | None, run: Run
) -> ChildOutcome:
    """Run `cmd` as a subprocess and return a `ChildOutcome`.

    The process is created synchronously so that `run.worker` can be set immediately for
    interrupts.
    Blocking I/O and the `os.wait4` reap are offloaded to a thread-pool executor
    so the event loop stays responsive.

    Using `subprocess.Popen` (rather than `asyncio.create_subprocess_*`)
    means asyncio's child watcher never registers this PID,
    so our `os.wait4` call captures per-process CPU time without racing against the watcher.
    """
    stdin = subprocess.PIPE if stdin_data is not None else subprocess.DEVNULL
    wtime_start = time.perf_counter()
    try:
        proc = subprocess.Popen(
            cmd,
            shell=shell,
            stdin=stdin,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            cwd=cwd,
            # Put the step in its own session, so that a Ctrl-C in the terminal does not
            # reach it directly and the director alone decides when to stop it.
            # This also makes the whole process tree of the step signallable as one group,
            # which a `sh -c` wrapper around a pipeline would otherwise hide.
            start_new_session=True,
        )
    except OSError as exc:
        return ChildOutcome(1, "", f"Failed to launch command {cmd!r}: {exc}\n")
    run.worker = SubprocessWorker(proc, job_i=run.job_i)
    try:
        loop = asyncio.get_running_loop()
        stdout, stderr, usage = await loop.run_in_executor(
            None, _communicate_wait4, proc, stdin_data, wtime_start
        )
    finally:
        run.worker = None
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
    """Run `cmd` as a subprocess with or without a shell.

    Returns a `ChildOutcome` whose `payload` is `(rc, stdout, stderr)`,
    with `stdout`/`stderr` decoded to `str`.

    Actual execution is performed by `_exec_subprocess`.
    """
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
    await await_fd_readable(conn.fileno())
    return conn.recv()


async def _wait_proc(proc):
    """Asynchronously wait for a multiprocessing process to exit, then reap it."""
    await await_fd_readable(proc.sentinel)
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
        # never gets here with exitcode 0, but a failed run must not report success.
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
    A child that dies without sending an outcome (typically because the build was aborted
    and it was killed with `SIGKILL`) yields a failed outcome describing how it died,
    just like a subprocess killed by a signal, instead of an `EOFError` escaping as an
    internal director error.
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
    """Capture OS-level fd 1/2 output (subprocesses, C extensions) that a `sys.stdout` /
    `sys.stderr` `StringIO` redirect does not see.

    Redirects fds 1 and 2 to pipes drained on background threads for the duration of the
    `with` block, then restores the original fds and appends whatever the drain threads
    read to `stdout_buf` / `stderr_buf`. Restoring (rather than closing) the original fds
    is what signals EOF to the drain threads, which is what unblocks their join.
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
        # Same semantics as _communicate_wait4: a step that leaves a daemon holding
        # the inherited fd open will keep this thread (and the join below) waiting.
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
    """Entry point for forkserver-launched Python executions.

    This function runs in a forked child process and sends a `ChildOutcome` back via
    `result_conn`, whose `payload` is a `(returncode, stdout, stderr)` tuple.
    When `ep_value` is `None`, `cmd` is a Python script path run via `runpy.run_path`,
    with local imports auto-detected and registered as dynamic inputs.
    When `ep_value` is a `module:attr` string, the corresponding console_script function
    is imported and called directly without import tracking.
    """
    # Put the step in its own session, the counterpart of `start_new_session` on the
    # subprocess path, so that both kinds of steps are signalled the same way: as a process
    # group, by the director alone. See `_signal_process_group`.
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
    # The inner try/except must run inside the `with` block, so that the fd restore and
    # append (in the `with` block's teardown) happen after the traceback (if any) has
    # already been written to stderr_buf, not before.
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
                # Run atexit handlers before sending the result so that any amend() calls
                # from atexit handlers are processed while the step is still RUNNING.
                # There is no public API for this in CPython; _run_exitfuncs is a stable
                # private implementation detail present in every CPython release since 2.0.
                with contextlib.suppress(AttributeError):
                    atexit._run_exitfuncs()
                if ep_value is None:
                    # Must be imported ONLY in the forked process:
                    # it opens a new connection to the director socket,
                    # which should happen in the forked process, not its parent.
                    from stepup.core.api import amend  # noqa: PLC0415

                    amend(inp=get_local_import_paths(script_path=Path(cmd)))
        except BaseException:  # noqa: BLE001
            # All exceptions must be caught here, to be able to send the corresponding
            # output and return code back to the director process.
            # Otherwise, the parent process would just see a connection error.
            traceback.print_exc(file=stderr_buf)
            returncode = 1
        finally:
            # Snapshot in a `finally`: a step failing with an uncaught exception skips the
            # tail of the `try` body, which would otherwise leave the usage at zero even
            # though the step did consume CPU time.
            ru_self_end = resource.getrusage(resource.RUSAGE_SELF)
            ru_children_end = resource.getrusage(resource.RUSAGE_CHILDREN)
    wtime_end = time.perf_counter()
    usage = ResourceUsage.from_diff(
        ru_self_start, ru_self_end, ru_children_start, ru_children_end, wtime_start, wtime_end
    )
    result_conn.send(ChildOutcome(returncode, stdout_buf.getvalue(), stderr_buf.getvalue(), usage))


PYCODE_WRAPPER = """\
import os
import sys
import runpy
from path import Path
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
# Command dispatching
#


async def launch_command(
    command: str,
    *,
    subshell: bool,
    env: dict,
    cwd: Path,
    mp_ctx: multiprocessing.context.BaseContext | None,
    run: Run,
) -> ChildOutcome:
    """Launch a step's command and return its `ChildOutcome`.

    Dispatches between a subshell, a Python script (`*.py`), a Python console_script
    entry point, or a plain (non-shell) exec. Python scripts and entry points run in a
    forkserver child when `mp_ctx` is not `None`, and as a plain subprocess otherwise.

    Parameters
    ----------
    command
        The command line to launch, as it would be typed in a shell.
    subshell
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
    """
    parts = shlex.split(command)
    if not parts:
        raise ValueError(f"Empty command: {command!r}")
    if subshell:
        return await _run_subprocess(command, shell=True, env=env, cwd=cwd, run=run)
    if parts[0].endswith(".py"):
        return await _run_python_script(parts[0], parts[1:], env, cwd, mp_ctx, run)
    try:
        ep_value = _detect_python_entrypoint(parts[0])
    except RunError as exc:
        return ChildOutcome(1, "", str(exc) + "\n")
    if ep_value is not None:
        return await _run_python_entrypoint(parts[0], parts[1:], ep_value, env, cwd, mp_ctx, run)
    return await _run_subprocess(parts, shell=False, env=env, cwd=cwd, run=run)
