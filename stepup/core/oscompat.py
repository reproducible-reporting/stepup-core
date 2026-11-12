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
    "af_unix_available",
    "forkserver_available",
    "perf_available",
    "raw_terminal_available",
    "suspend_available",
)


def af_unix_available() -> bool:
    """Whether Unix domain sockets exist, which `asyncio` only supports where `socket` does."""
    return hasattr(socket, "AF_UNIX")


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


def suspend_available() -> bool:
    """Whether a process can be suspended with Ctrl-Z, which needs the job-control signals."""
    return all(hasattr(signal, name) for name in ("SIGTSTP", "SIGSTOP", "SIGCONT"))
