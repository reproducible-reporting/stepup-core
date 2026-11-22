# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Forward terminal signals and keystrokes from `stepup build` to the director."""

import asyncio
import contextlib
import os
import signal
import sys
import threading
import time
from collections.abc import AsyncGenerator
from typing import Self

import attrs

from ..constants import DIRECTOR_LOG_DESCRIPTION
from ..enums import ReturnCode
from ..exceptions import RPCError, UsageError
from ..path import Path
from ..platform.asyncio import iter_until_stopped
from ..platform.oscompat import raw_terminal_available, windows_console_available
from ..platform.stdio import is_terminal
from ..rpc.client import SocketAsyncRPCClient
from .reporter import ReporterHandler

if raw_terminal_available():
    import termios
if windows_console_available():
    from ..platform.winapi import read_console_keys

__all__ = (
    "RawTerminal",
    "SuspendHandler",
    "TerminalSignalHandler",
    "keyboard",
    "stdin_has_keystrokes",
)


#
# Signal handling
#


_DIRECTOR_KILL_GRACE = 15.0
"""Seconds the director gets to shut down after a terminal signal, before it is killed.

Comfortably longer than the director's own `INTERRUPT_GRACE`,
so that killing steps and reporting the outcome is tried first.
"""


@attrs.define
class TerminalSignalHandler:
    """Handle terminal signals received while the director subprocess is running.

    The director aborts the build on such a signal itself (see `DirectorHandler.interrupt`),
    so the work here is only to make sure it receives the signal,
    and to guarantee that the terminal user interface exits even when the director does not.
    """

    reporter_handler: ReporterHandler = attrs.field()
    """The reporter used to inform the user about the interruption."""

    process_director: asyncio.subprocess.Process = attrs.field()
    """The director subprocess, to forward signals to."""

    sig: signal.Signals | None = attrs.field(init=False, default=None)
    """The first terminal signal received, or `None` if the build was not interrupted."""

    _count: int = attrs.field(init=False, default=0)
    """The number of terminal signals received so far."""

    _kill_handle: asyncio.TimerHandle | None = attrs.field(init=False, default=None)
    """Handle of the scheduled `_kill_director` call, or `None` when none is pending."""

    _killed: bool = attrs.field(init=False, default=False)
    """Whether this handler killed the director."""

    def handle(self, sig: signal.Signals) -> None:
        """React to a terminal signal, escalating when it is received repeatedly."""
        self._count += 1
        if self._count == 1:
            self.sig = sig
            self.reporter_handler.report(
                "WARNING", f"Interrupted by {sig.name}. Waiting for the director to stop.", []
            )
        if self._count >= 3:
            self.reporter_handler.report("ERROR", f"Killing the director ({_KILL_MECHANISM}).", [])
            self._kill_director()
            return
        if not _terminal_broadcasts(sig, self.process_director.pid):
            # Only this process received the signal, so the director must be told separately.
            with contextlib.suppress(ProcessLookupError):
                self.process_director.send_signal(sig)
        self._arm_kill_timer()

    def cancel_kill(self) -> None:
        """Cancel the pending last-resort kill, e.g. because the director exited in time."""
        if self._kill_handle is not None:
            self._kill_handle.cancel()
            self._kill_handle = None

    def translate_wait_status(self, wait_status: int) -> int:
        """Turn the director's wait status into a `ReturnCode` flag combination.

        The corrections applied here are about signals,
        which is why this lives on the signal handler:
        the director may have been killed, and this process may have received a signal.

        Parameters
        ----------
        wait_status
            What `Process.wait()` returned: the director's exit code,
            or, on POSIX, minus the number of the signal that killed it.

        Returns
        -------
        returncode
            The director's exit code,
            with `ReturnCode.INTERRUPTED` OR-ed in when a terminal signal was received.
            It is replaced by `ReturnCode.INTERNAL` when the director did not get to decide it:
            when it was killed, or when the wait status is not a `ReturnCode` combination,
            e.g. the status `0xC000013A` of a Windows process that a console event ended.
        """
        if wait_status < 0:
            signal_name = signal.Signals(-wait_status).name
            self.reporter_handler.report("ERROR", f"Director killed by {signal_name}", [])
            returncode = ReturnCode.INTERNAL.value
        elif self._killed and not hasattr(signal, "SIGKILL"):
            # Where a killed process has no signal to show for it,
            # its exit code is whatever its killer picked.
            self.reporter_handler.report("ERROR", f"Director killed by {_KILL_MECHANISM}", [])
            returncode = ReturnCode.INTERNAL.value
        elif not _is_return_code(wait_status):
            self.reporter_handler.report(
                "ERROR", f"Director exited with unexpected code 0x{wait_status:08X}", []
            )
            returncode = ReturnCode.INTERNAL.value
        else:
            returncode = wait_status
        if self.sig is not None:
            # Record that the build was aborted, on top of whatever the director reported.
            # Without this, an interrupted build is indistinguishable from a completed one.
            returncode |= ReturnCode.INTERRUPTED.value
        return returncode

    def _arm_kill_timer(self) -> None:
        """Schedule the last-resort kill, unless it is already scheduled.

        This guarantees the user gets the shell prompt back
        even when the director gets stuck on its way out.
        """
        if self._kill_handle is None:
            self._kill_handle = asyncio.get_running_loop().call_later(
                _DIRECTOR_KILL_GRACE, self._kill_director
            )

    def _kill_director(self) -> None:
        self.cancel_kill()
        with contextlib.suppress(ProcessLookupError):
            self.process_director.kill()
            self._killed = True


def _is_return_code(wait_status: int) -> bool:
    """Whether a non-negative wait status is a combination of `ReturnCode` flags."""
    try:
        ReturnCode(wait_status)
    except ValueError:
        return False
    return True


_KILL_MECHANISM = "SIGKILL" if hasattr(signal, "SIGKILL") else "TerminateProcess"
"""What `Process.kill()` uses to end the director, for the messages to the user."""


@attrs.define
class SuspendHandler:
    """Handle a `SIGTSTP` (Ctrl-Z) received while the director subprocess is running.

    A suspension is not an interruption:
    unlike `TerminalSignalHandler`, this leaves the return code alone
    and never gives up on the director.
    The director stops its own steps and itself (see `DirectorHandler.suspend`),
    so the work here is to hand the terminal back to the shell in a usable state
    and to take it back afterwards.
    """

    reporter_handler: ReporterHandler = attrs.field()
    """The reporter whose live display must be stopped for the duration of the suspension."""

    process_director: asyncio.subprocess.Process = attrs.field()
    """The director subprocess, to forward signals to."""

    raw_terminal: "RawTerminal | None" = attrs.field(default=None)
    """The terminal to hand back and take over again, or `None` when not interactive."""

    def handle(self) -> None:
        """Suspend this process, and resume when it is continued.

        The default disposition is restored to actually stop,
        since a handled `SIGTSTP` does not stop anything.
        Execution continues after `os.kill` once the shell continues the process group,
        which is where the terminal is taken over again.
        """
        self.reporter_handler.suspend_display()
        if self.raw_terminal is not None:
            self.raw_terminal.suspend()
        forwarded = not _terminal_broadcasts(signal.SIGTSTP, self.process_director.pid)
        if forwarded:
            # Only this process received the signal, so the director must be told separately.
            with contextlib.suppress(ProcessLookupError):
                self.process_director.send_signal(signal.SIGTSTP)
        loop = asyncio.get_running_loop()
        loop.remove_signal_handler(signal.SIGTSTP)
        wtime_start = time.perf_counter()
        try:
            os.kill(os.getpid(), signal.SIGTSTP)
        finally:
            suspended_seconds = time.perf_counter() - wtime_start
            loop.add_signal_handler(signal.SIGTSTP, self.handle)
            if forwarded:
                # The shell continues the process group of its job.
                # That group does not include a director
                # that never received the suspension from the terminal.
                with contextlib.suppress(ProcessLookupError):
                    self.process_director.send_signal(signal.SIGCONT)
            if self.raw_terminal is not None:
                self.raw_terminal.resume()
            self.reporter_handler.resume_display(suspended_seconds)


_TERMINAL_SIGNALS = frozenset(
    getattr(signal, name) for name in ("SIGINT", "SIGTSTP", "SIGBREAK") if hasattr(signal, name)
)
"""The signals StepUp handles that the terminal can generate itself.

`SIGTSTP` only exists on platforms with job control,
and `SIGBREAK` only where the terminal is a Windows console.
"""


def _terminal_broadcasts(sig: signal.Signals, pid: int) -> bool:
    """Check whether the terminal driver already sent `sig` to `pid` as well as to this process.

    Of the signals StepUp handles,
    only `SIGINT` (Ctrl-C) and `SIGTSTP` (Ctrl-Z) are generated by the terminal driver,
    which sends them to every process in the foreground process group at once.
    That is the case exactly when this process and `pid`
    are both in the process group that owns the controlling terminal.
    A `SIGINT` or `SIGTSTP` sent explicitly (`kill -INT`, `kill -TSTP`) to a backgrounded StepUp
    does not qualify, and neither does any `SIGTERM`.

    A Windows console has no such distinction.
    `SIGINT` and `SIGBREAK` stand for console control events (Ctrl-C and Ctrl-Break),
    which cannot be sent to a single process:
    the console delivers them to all its processes, or to all those of one process group.
    The director is attached to the same console and is in the same process group,
    so it always received what this process received.

    Parameters
    ----------
    sig
        The signal that was received by this process.
    pid
        The process id of the director.

    Returns
    -------
    broadcast
        `True` if `pid` received `sig` from the terminal driver as well.
    """
    if sig not in _TERMINAL_SIGNALS:
        return False
    if windows_console_available():
        return True
    try:
        # Query the controlling terminal itself:
        # stdin may be redirected while the process is still in the foreground process group.
        with open("/dev/tty", encoding="utf-8") as tty:
            foreground_pgid = os.tcgetpgrp(tty.fileno())
        return foreground_pgid == os.getpgid(0) == os.getpgid(pid)
    except OSError:
        return False


#
# Keyboard
#


@attrs.define
class KeyAction:
    """A single keystroke's dispatch entry: RPC call, reported message, and help label."""

    method: str = attrs.field(kw_only=True)
    """The name of the `DirectorHandler` RPC method to call."""

    label: str = attrs.field(kw_only=True)
    """The short label for this key shown in `_KEY_STROKE_HELP`."""

    description: str = attrs.field(kw_only=True)
    """A longer description of what the key does."""

    message: str | None = attrs.field(kw_only=True, default=None)
    """The message reported to the terminal."""

    args: tuple = attrs.field(kw_only=True, default=())
    """Positional arguments passed to the RPC call."""

    kwargs: dict = attrs.field(kw_only=True, factory=dict)
    """Keyword arguments passed to the RPC call."""


_KEY_ACTIONS = {
    "g": KeyAction(
        method="write_graph",
        label="graph",
        description="Write the workflow graph to graph.txt.",
        args=("graph",),
    ),
    "d": KeyAction(
        method="drain",
        label="drain",
        description="Drain the scheduler. (Leaves build phase.)",
        message="Draining the scheduler.",
    ),
    "j": KeyAction(
        method="wait_and_shutdown",
        label="join",
        description="Wait for all steps to complete before shutting down.",
        message="Waiting for all steps before shutdown.",
    ),
    "q": KeyAction(
        method="shutdown",
        label="shutdown",
        description="Shut down StepUp. (1st is graceful. 2nd kills steps.)",
        message="Shutting down.",
    ),
    "r": KeyAction(
        method="start_build_phase",
        label="rebuild",
        description="Restart the builder. (Leaves watch phase.)",
        message="Restarting the builder.",
    ),
    "R": KeyAction(
        method="start_build_phase",
        label="recheck",
        description="Like r, and also rerun steps with recheck=True.",
        message="Restarting the builder and rerunning the steps with recheck=True.",
        kwargs={"recheck": True},
    ),
}

_KEY_STROKE_HELP = "\n".join(
    f"  {key} = {action.label:<10s}  {action.description}" for key, action in _KEY_ACTIONS.items()
)


def stdin_has_keystrokes() -> bool:
    """Whether single keystrokes can be read from the standard input of this process."""
    return (raw_terminal_available() or windows_console_available()) and is_terminal(sys.stdin)


@attrs.define
class RawTerminal:
    """Owner of the terminal attributes of `fd` while keyboard interaction is active.

    On a POSIX terminal,
    single keystrokes can only be read when canonical input and echo are disabled,
    which is a process-wide change to a terminal shared with the shell.
    This class owns the attributes it found,
    so that the same state can be handed back on the way out (`leave`)
    and for the duration of a suspension (`suspend`).
    """

    fd: int = attrs.field()
    """The file descriptor of the terminal to reconfigure."""

    _saved: list | None = attrs.field(init=False, default=None)
    """The attributes found by `enter`, or `None` when this terminal is not reconfigured."""

    def __enter__(self) -> Self:
        self.enter()
        return self

    def __exit__(self, exc_type, exc_value, tb) -> None:
        self.leave()

    def enter(self) -> None:
        """Disable canonical input and echo, remembering the attributes found."""
        self._saved = termios.tcgetattr(self.fd)
        self._apply_raw()

    def leave(self) -> None:
        """Restore the attributes found by `enter`, for good."""
        if self._saved is not None:
            termios.tcsetattr(self.fd, termios.TCSADRAIN, self._saved)
            self._saved = None

    def suspend(self) -> None:
        """Restore the attributes found by `enter`, to be undone by `resume`.

        The shell takes the terminal back while StepUp is suspended,
        and it must not find it in a mode meant for single keystrokes.
        """
        if self._saved is not None:
            termios.tcsetattr(self.fd, termios.TCSADRAIN, self._saved)

    def resume(self) -> None:
        """Disable canonical input and echo again, after a `suspend`.

        This is not optional bookkeeping:
        shells do not reliably restore the attributes of a job they continue,
        so without this the keyboard interface silently stops working after a Ctrl-Z,
        with every keystroke echoed and swallowed by the line buffer.
        """
        if self._saved is not None:
            self._apply_raw()

    def _apply_raw(self) -> None:
        """Clear the `ICANON` and `ECHO` flags on the terminal."""
        tcattr = termios.tcgetattr(self.fd)
        tcattr[3] &= ~(termios.ICANON | termios.ECHO)
        termios.tcsetattr(self.fd, termios.TCSAFLUSH, tcattr)


_CONSOLE_POLL_INTERVAL = 0.1
"""Seconds after which the thread that reads keys from a Windows console checks if it must stop."""


async def _iter_keystrokes(stop_event: asyncio.Event) -> AsyncGenerator[str, None]:
    """Yield keystrokes from stdin, one at a time, until `stop_event` is set.

    Keystrokes come from the stream that is `sys.stdin` when the iteration starts.
    A POSIX terminal must already be in raw mode, see `RawTerminal`.
    A Windows console needs no preparation, see `read_console_keys`.

    Reads happen in a background thread.
    On POSIX, this is because putting stdin in non-blocking mode
    (e.g. via `asyncio.StreamReader`) also affects stdout and stderr
    when they share the same underlying open file description as stdin,
    which breaks `print` and `rich.print` for large output.
    On Windows, the event loop cannot wait for console input.

    On POSIX, the background thread may still be blocked in a read of stdin
    after this generator closes,
    since there is no portable way to interrupt a blocking read on stdin from another thread.
    It is daemonic and the process is about to exit anyway,
    so it consumes at most one keystroke typed between the end of the build and process exit.
    On Windows, the thread has ended when this generator closes.
    """
    loop = asyncio.get_running_loop()
    queue = asyncio.Queue()
    # The POSIX thread may outlive this generator,
    # so it must not read from a stream that replaces `sys.stdin` later.
    stdin = sys.stdin

    def _stdin_loop():
        while True:
            ch = stdin.read(1)
            # read() keeps returning an empty string when stdin is closed, without blocking.
            if ch == "":
                break
            loop.call_soon_threadsafe(queue.put_nowait, ch)

    stop_reading = threading.Event()

    def _console_loop():
        fd = stdin.fileno()
        # Without a console that can be read, there are no keys to wait for,
        # which is no reason to disturb the build.
        with contextlib.suppress(OSError):
            while not stop_reading.is_set():
                for ch in read_console_keys(fd, _CONSOLE_POLL_INTERVAL):
                    loop.call_soon_threadsafe(queue.put_nowait, ch)

    if windows_console_available():
        thread = threading.Thread(target=_console_loop, daemon=True)
        thread.start()
        try:
            async for ch in iter_until_stopped(queue.get, stop_event):
                yield ch
        finally:
            # The thread must not outlive the event loop it reports to.
            stop_reading.set()
            await asyncio.to_thread(thread.join)
    else:
        threading.Thread(target=_stdin_loop, daemon=True).start()
        async for ch in iter_until_stopped(queue.get, stop_event):
            yield ch


async def keyboard(
    director_socket_path: Path,
    reporter_handler: ReporterHandler,
    stop_event: asyncio.Event,
) -> None:
    """Read keystrokes from the terminal and dispatch them as RPC calls to the director.

    Runs until `stop_event` is set.
    An unrecognized key is reported back to the user together with `_KEY_STROKE_HELP`,
    instead of being silently ignored.

    No keystroke may take the build down with it,
    so every failure of a dispatched call is reported and the loop continues.

    Parameters
    ----------
    director_socket_path
        The socket at which the director listens for instructions.
    reporter_handler
        The reporter used to inform the user which key was dispatched,
        that a key is unsupported, or that its call failed.
    stop_event
        Set to stop reading keystrokes.
    """
    async for ch in _iter_keystrokes(stop_event):
        action = _KEY_ACTIONS.get(ch)
        if action is None:
            pages = [("Keys", _KEY_STROKE_HELP)]
            reporter_handler.report("KEYBOARD", f"Unsupported key {ch}", pages)
            continue
        try:
            async with SocketAsyncRPCClient(
                director_socket_path, server_log_description=DIRECTOR_LOG_DESCRIPTION
            ) as client:
                method = getattr(client.call, action.method)
                if action.message is not None:
                    reporter_handler.report("KEYBOARD", action.message, [])
                await method(*action.args, **action.kwargs)
        except OSError as exc:
            # The director is on its way out (socket unlinked, or listening backlog gone),
            # or the connection dropped mid-call.
            # Losing a keystroke at that point is expected,
            # so report it and keep reading until stop_event is set.
            reporter_handler.report(
                "KEYBOARD", f"Director unreachable, key {ch} ignored: {exc}", []
            )
        except UsageError as exc:
            # The director rejected the call itself, e.g. because of an invalid argument.
            # Such an error carries a one-line message and no traceback,
            # so it is reported under a "Message" heading instead of the "Traceback" below.
            reporter_handler.report(
                "ERROR", f"Key {ch} failed in the director.", [("Message", str(exc).strip())]
            )
        except RPCError as exc:
            # The call did reach the director, but raised there,
            # e.g. `graph` could not write its output files.
            # Unlike an unreachable director, this is a real error worth landing in the fail log,
            # but it is still only this keystroke that failed.
            reporter_handler.report(
                "ERROR", f"Key {ch} failed in the director.", [("Traceback", str(exc).strip())]
            )
