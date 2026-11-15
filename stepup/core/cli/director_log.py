# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Find a running director through its log, and check the log for problems."""

import contextlib
import logging
import os
import re
import sys

from ..constants import DIRECTOR_LOG_PID_PREFIX, DIRECTOR_LOG_SOCKET_PREFIX
from ..path import Path

if sys.platform == "win32":
    from ..platform.winapi import is_process_alive

__all__ = (
    "is_process_running",
    "query_director_log",
    "scan_director_log",
)

logger = logging.getLogger(__name__)


def is_process_running(pid: int) -> bool:
    """Check whether a process with this pid exists. (It may or may not be the director.)

    Together with `query_director_log`, this tells a live director from a stale socket.

    A pid can be recycled, so a `True` answer does not prove the process is the director.
    Treat it conservatively: waiting for a director or refusing to start a build is harmless,
    two directors on one database are not.
    """
    if sys.platform == "win32":
        # `os.kill(pid, 0)` is no liveness check on Windows,
        # where signal 0 is `CTRL_C_EVENT` and is sent to the console instead.
        return is_process_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # The process exists but belongs to another user.
        return True
    return True


def query_director_log(director_log: Path) -> tuple[Path | None, int | None, str]:
    """Look up the director's socket and pid in `DIRECTOR_LOG`.

    Parameters
    ----------
    director_log
        The path of the director log to read from.

    Returns
    -------
    socket_path
        The socket path advertised by the director if it exists on disk, and `None` otherwise.
        It is a Unix domain socket or an endpoint file, see `stepup.core.rpc.transport`.
    pid
        The pid advertised by the director,
        or `None` when the log holds no usable `PID` line.
    message
        An explanation of why no existing socket was found, empty when one was.
    """
    try:
        with open(director_log, encoding="utf-8") as fh:
            line_socket = fh.readline()
            line_pid = fh.readline()
    except OSError as exc:
        return None, None, f"File {director_log} could not be read: {exc}"

    # Validate the header and return socket_path only if validated.
    # PID can be useful even if the socket is stale, so it is returned regardless.
    pid = None
    if line_pid.startswith(DIRECTOR_LOG_PID_PREFIX):
        with contextlib.suppress(ValueError):
            pid = int(line_pid.removeprefix(DIRECTOR_LOG_PID_PREFIX))
    if not line_socket.startswith(DIRECTOR_LOG_SOCKET_PREFIX):
        return None, pid, f"File {director_log} does not start with SOCKET line."

    socket_path = Path(line_socket.removeprefix(DIRECTOR_LOG_SOCKET_PREFIX).strip())
    if socket_path and socket_path.exists():
        return socket_path, pid, ""
    message = f"Socket {socket_path} read from {director_log} does not exist. StepUp not running?"
    return None, pid, message


DIRECTOR_LOG_CHECKS = (
    # The name is a `__qualname__`, so it contains dots for methods and nested functions.
    (re.compile(r"coroutine '[^']+' was never awaited"), "Unawaited coroutine"),
    (re.compile(r"\w+ exception was never retrieved"), "Unretrieved exception"),
    (re.compile("Task was destroyed but it is pending!"), "Abandoned pending task"),
    (re.compile("Exception in callback"), "Exception in callback"),
    (re.compile("Exception in thread"), "Exception in thread"),
    (re.compile("Exception ignored"), "Ignored exception"),
    (
        re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\s+(?:ERROR|CRITICAL)\s"),
        "Logged error",
    ),
)
"""Patterns for lines in `DIRECTOR_LOG` that betray an internal problem, each with a label.

All but the last are verbatim CPython wordings for work that was left dangling:
a coroutine that was never awaited,
a `Future` or `Task` collected while holding an unhandled exception,
a task destroyed while still pending,
and an exception that escaped a callback, a thread or a `__del__`
(the unraisable hook prints `Exception ignored ...`).
None of these make the director exit with a non-zero return code,
which is precisely why the log must be scanned for them after the fact.
They are matched case-sensitively, since they are copied from CPython's sources.

The last pattern is of a different nature:
it matches any log record at level `ERROR` or `CRITICAL`,
following the `format`/`datefmt` that `async_main` (`director/__main__.py`)
hands to `logging.basicConfig`.
Matching the level field rather than the word anywhere in the line
is what keeps the director's own header lines out of it:
`LOG_LEVEL ERROR` is not an error, it is a build started with `--log-level=ERROR`.
It comes last because `scan_director_log` labels a line with the first check that matches,
and the messages above are also logged at `ERROR` level when `asyncio` reports them,
in which case the specific label is the more informative one.

Warning categories that any third-party module can raise
(`DeprecationWarning`, `ResourceWarning`, ...) are deliberately absent:
they are ignored by Python's default filters,
so a match would say more about the warning filter than about StepUp.
"""


def scan_director_log(path_director_log: Path) -> list[str]:
    """Collect the lines in the director log that match one of `DIRECTOR_LOG_CHECKS`.

    Parameters
    ----------
    path_director_log
        The path of the director log to read from.
        A log that cannot be opened (most commonly because it does not exist)
        yields no findings.

    Returns
    -------
    findings
        One `"{label}: {line}"` string per matching line, in the order of the log.
        Empty when the director log is clean, which is the expected outcome of every build.
    """
    # As in `query_director_log`, the log is opened without testing for its existence first,
    # because a director starting up in parallel may remove it between the test and the open.
    findings = []
    try:
        with open(path_director_log, encoding="utf-8") as fh:
            for line in fh:
                for pattern, label in DIRECTOR_LOG_CHECKS:
                    if pattern.search(line) is not None:
                        findings.append(f"{label}: {line.strip()}")
                        break
    except OSError:
        return []
    return findings
