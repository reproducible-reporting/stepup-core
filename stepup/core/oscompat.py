# SPDX-FileCopyrightText: 2026 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Facts about the operating system that decide which optional features StepUp can offer.

Each predicate answers one question about the platform,
by testing for the feature itself where Python offers such a test,
so that the answer stays right on platforms nobody thought of.
A feature that the user enables on a platform without it must fail with a `UsageError`
that says so, instead of crashing later with an `ImportError` or `AttributeError`.
Tests replace these predicates to cover both answers on one platform.
"""

import importlib.util
import multiprocessing
import signal
import socket
import sys

__all__ = (
    "abort_signals",
    "af_unix_available",
    "file_modes_available",
    "forkserver_available",
    "perf_available",
    "raw_terminal_available",
    "suspend_available",
    "windows_console_available",
)


def abort_signals() -> tuple[signal.Signals, ...]:
    """The signals that ask a process to stop, of those that the platform can deliver.

    Windows defines `SIGTERM` without ever delivering it,
    because a process is terminated there without being told.
    It has `SIGBREAK` instead, which is how Python reports a Ctrl-Break.
    """
    if hasattr(signal, "SIGBREAK"):
        return (signal.SIGINT, signal.SIGBREAK)
    return (signal.SIGINT, signal.SIGTERM)


def af_unix_available() -> bool:
    """Whether Unix domain sockets exist, which `asyncio` only supports where `socket` does."""
    return hasattr(socket, "AF_UNIX")


def file_modes_available() -> bool:
    """Whether files have permission bits, of which one makes a file executable.

    Windows has none: `os.chmod` only changes the read-only attribute there,
    and the mode that `os.stat` reports is derived from that attribute and from the file name.
    """
    return sys.platform != "win32"


def forkserver_available() -> bool:
    """Whether `multiprocessing` offers the forkserver start method."""
    return "forkserver" in multiprocessing.get_all_start_methods()


def perf_available() -> bool:
    """Whether the platform is one on which the `perf` profiler exists, i.e. Linux.

    This does not check that `perf` is installed.
    """
    return sys.platform == "linux"


def raw_terminal_available() -> bool:
    """Whether single keystrokes can be read from a terminal through the `termios` module."""
    return importlib.util.find_spec("termios") is not None


def windows_console_available() -> bool:
    """Whether the terminal is a Windows console instead of a POSIX terminal.

    A console hands out single keystrokes as input records, without a raw mode,
    and delivers a Ctrl-C or Ctrl-Break to every process attached to it.
    """
    return sys.platform == "win32"


def suspend_available() -> bool:
    """Whether a process can be suspended with Ctrl-Z, which needs the job-control signals."""
    return all(hasattr(signal, name) for name in ("SIGTSTP", "SIGSTOP", "SIGCONT"))
