# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""The in-flight work of a step, which can be interrupted, and the state of a `Run`.

`Worker` is the base class of such work.
Its subclasses stop a subprocess, a forkserver child or a computation in a thread.
"""

import asyncio
import contextlib
import logging
import os
import signal
import subprocess
import sys
import threading
from collections.abc import Callable
from typing import Any

import attrs

from ..enums import StopLevel
from ..exceptions import WorkAbandonedError
from ..graph.outcome import ChildOutcome
from ..graph.step import Step
from ..platform.oscompat import suspend_available
from ..utils import escape_control_chars

if sys.platform == "win32":
    from ..platform.winapi import JobObject

__all__ = (
    "WINDOWS_KILL_EXIT_CODE",
    "ForkserverWorker",
    "Run",
    "SubprocessWorker",
    "ThreadWorker",
    "Worker",
    "describe_stop_level",
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

    _exited: asyncio.Future = attrs.field(init=False)
    """The future that is resolved when `work` has returned or raised."""

    _abandoned: asyncio.Future = attrs.field(init=False)
    """The future that is resolved when `interrupt` gives up waiting for the thread."""

    _thread: threading.Thread = attrs.field(init=False)
    """The dedicated thread that runs `work`."""

    def __attrs_post_init__(self):
        self._loop = asyncio.get_running_loop()
        self._future = self._loop.create_future()
        self._exited = self._loop.create_future()
        self._abandoned = self._loop.create_future()
        self._thread = threading.Thread(target=self._run, daemon=True)

    async def run_in_thread(self) -> Any:
        """Start the thread, wait for its result and return it, re-raising its exception.

        When the waiting task is cancelled, the thread is still waited for,
        until it returns or until it is abandoned.

        Raises
        ------
        WorkAbandonedError
            When `interrupt` abandoned the thread before it returned.
        """
        self._thread.start()
        try:
            await asyncio.wait({self._future, self._abandoned}, return_when=asyncio.FIRST_COMPLETED)
            if not self._future.done():
                raise WorkAbandonedError(
                    f"Abandoned the background compute thread of job {self.job_i}."
                )
            return self._future.result()
        finally:
            self._cancel_event.set()
            # Unlike awaiting it, `asyncio.wait` leaves the future pending when the task is
            # cancelled, and a result that arrives after this is dropped by `_resolve`.
            self._future.cancel()
            if not (self._exited.done() or self._abandoned.done()):
                await asyncio.wait(
                    {self._exited, self._abandoned}, return_when=asyncio.FIRST_COMPLETED
                )

    def interrupt(self, level: StopLevel) -> None:
        """Cancel the computation, and at `StopLevel.KILL` also stop waiting for the thread.

        A thread cannot be killed.
        An abandoned thread keeps running until it notices the cancellation,
        e.g. when a system call that blocks it returns, or until the director exits.
        """
        logger.info("Cancelling background compute thread for job %d", self.job_i)
        self._cancel_event.set()
        if level == StopLevel.KILL and not self._abandoned.done():
            if self._thread.is_alive():
                logger.warning(
                    "Abandoning the unresponsive background compute thread for job %d", self.job_i
                )
            self._abandoned.set_result(None)

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
        # An abandoned thread may outlive the event loop,
        # whose `call_soon_threadsafe` then raises `RuntimeError`.
        with contextlib.suppress(RuntimeError):
            self._loop.call_soon_threadsafe(self._resolve, outcome, error)

    def _resolve(self, outcome: Any, error: BaseException | None) -> None:
        self._exited.set_result(None)
        # The future is cancelled when `run_in_thread` stopped waiting for the result,
        # and `set_result`/`set_exception` would then raise `InvalidStateError`.
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
    `forkserver_entry` for forkserver children.
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
