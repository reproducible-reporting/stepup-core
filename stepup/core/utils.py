# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Small utilities used throughout."""

import contextlib
import csv
import logging
import os
import re
import shlex
import string
from collections.abc import Iterable
from time import monotonic_ns

from path import Path

from .constants import JOBLOG_CSV
from .exceptions import PathError, StepUpError

__all__ = (
    "DIRECTOR_LOG_CHECKS",
    "JOBLOG_COLUMNS",
    "CaseSensitiveTemplate",
    "escape_command_display",
    "extract_env_overrides",
    "format_command",
    "format_digest",
    "format_subprocess",
    "is_debug",
    "is_process_running",
    "merge_resources",
    "parse_resources",
    "positive_int",
    "query_director_log",
    "reset_joblog",
    "scan_director_log",
    "string_to_bool",
    "string_to_list",
    "write_joblog_record",
)


logger = logging.getLogger(__name__)


#
# Miscellaneous
#


class CaseSensitiveTemplate(string.Template):
    """A case sensitive Template class suitable for StepUp.

    - Accepts named wildcards ${*foo}.
    - Accepts upper and lower case variables.
    """

    flags = re.NOFLAG
    idpattern = r"(?a:[*]?[_a-zA-Z][_a-zA-Z0-9]*)"


def format_digest(digest: bytes) -> str:
    hexdigest = digest.hex()
    return " ".join(hexdigest[i : i + 8] for i in range(0, 64, 8))


def format_command(executable: str) -> str:
    """Format a relative path to a local executable for execution in a shell."""
    executable = Path(executable)
    if executable.isabs():
        raise PathError(f"Executable is not a relative path: {executable}")
    relative = executable if executable.startswith(("./", "../")) else "." / executable
    return shlex.quote(relative)


# Matches a single leading `NAME=value` assignment in a command string, anchored at the scan
# position. The value may be unquoted, single-quoted, or double-quoted (shell-style).
_LEADING_ASSIGNMENT = re.compile(
    r"""
    \s*                                    # optional leading whitespace
    (?P<name>[A-Za-z_][A-Za-z0-9_]*)       # variable name
    =                                      # the equals sign
    (?P<value>
        (?:"(?:[^"\\]|\\.)*")              # double-quoted value
        | (?:'[^']*')                      # single-quoted value
        | [^\s'"]*                         # bare value (no whitespace or quotes)
    )
    (?=\s|$)                               # must be followed by whitespace or end of string
    """,
    re.VERBOSE,
)


def extract_env_overrides(command: str) -> tuple[dict[str, str] | None, str]:
    """Split leading `VAR=value` assignments off a command string.

    Only assignments at the very start of the command are extracted.
    Scanning stops at the first token that is not an assignment (e.g. the executable),
    so `./cmd FOO=bar` extracts nothing.
    Values may be unquoted, single-quoted, or double-quoted, consistent with shell quoting.

    Parameters
    ----------
    command
        The raw command string, possibly prefixed with `VAR=value` assignments.

    Returns
    -------
    env_overrides
        A dictionary with the extracted environment variable overrides.
    remaining
        The command string with the leading assignments removed, otherwise preserved verbatim.
    """
    env_overrides = {}
    pos = 0
    while True:
        match = _LEADING_ASSIGNMENT.match(command, pos)
        if match is None:
            break
        try:
            dequoted = shlex.split(match.group("value"))
        except ValueError:
            break
        env_overrides[match.group("name")] = dequoted[0] if dequoted else ""
        pos = match.end()
    if len(env_overrides) == 0:
        env_overrides = None
    return env_overrides, command[pos:].lstrip()


_ANSI_C_ESCAPES = {
    "\a": r"\a",
    "\b": r"\b",
    "\t": r"\t",
    "\n": r"\n",
    "\v": r"\v",
    "\f": r"\f",
    "\r": r"\r",
    "\x1b": r"\e",
}


def escape_command_display(command: str) -> str:
    """Rewrite control characters in a command line as `$'...'`-quoted escapes.

    An embedded control character (e.g. a literal newline) is spliced in as a
    `$'\\n'`-style ANSI-C-quoted escape, closing and reopening whichever quote is
    currently open around it. This keeps the result on a single line and, because
    adjacent shell tokens with no separator are concatenated, copy-pasting it into
    a POSIX shell reproduces `command` byte for byte.

    Parameters
    ----------
    command
        A shell command line, as passed to `step()` or `run()`.

    Returns
    -------
    escaped
        A single-line, shell-pasteable version of `command`.

    Notes
    -----
    This is a single-pass scanner that only tracks top-level single/double-quote
    nesting and backslash escaping. It does not recurse into nested `$(...)` or
    backtick command substitutions, so a control character embedded inside such a
    substitution's own quotes may be spliced incorrectly. This only affects how the
    command is displayed; the command is stored and executed verbatim.
    """
    pieces = []
    quote = None  # None, "'", or '"'
    escaped = False
    for char in command:
        if ord(char) < 0x20 or ord(char) == 0x7F:
            token = _ANSI_C_ESCAPES.get(char, f"\\x{ord(char):02x}")
            pieces.append(f"$'{token}'" if quote is None else f"{quote}$'{token}'{quote}")
            escaped = False
            continue
        pieces.append(char)
        if escaped:
            escaped = False
        elif quote == "'":
            if char == "'":
                quote = None
        elif char == "\\":
            escaped = True
        elif quote == '"':
            if char == '"':
                quote = None
        elif char in ("'", '"'):
            quote = char
    return "".join(pieces)


def format_subprocess(
    cmd: str,
    workdir: str,
    env: dict[str, str] | None,
    returncode: int | None,
    *,
    shell: bool = False,
) -> str:
    """Format a recorded subprocess invocation as a single, shell-pasteable line.

    The result is informative, not authoritative: the command line is shown verbatim
    (the wrapper that recorded it is responsible for quoting), an environment overlay becomes
    a `VAR=value` assignment prefix, a non-default working directory is rendered with a
    `(cd ... && ...)` shell wrapper (this is also reused to display failed step commands),
    and a non-zero exit code is appended as a trailing shell comment.

    Parameters
    ----------
    cmd
        The command line, as a single shell-quoted string.
    workdir
        The working directory of the subprocess, relative to `STEPUP_ROOT`.
    env
        The environment overlay (variables set on top of the inherited environment),
        or `None` when no overlay was applied.
    returncode
        The exit code of the subprocess, or `None` when the subprocess was not run.
    shell
        Whether `cmd` is a shell command line (as in `subprocess.run(..., shell=True)`).
        When `True`, a `cd` wrapper groups `cmd` in an extra `(...)` so a compound command
        stays gated on the `cd` succeeding; a single command needs no such grouping.

    Returns
    -------
    line
        A single-line, shell-pasteable representation of the invocation.
    """
    parts = []
    if env:
        parts.extend(f"{key}={shlex.quote(value)}" for key, value in env.items())
    parts.append(escape_command_display(cmd))
    line = " ".join(parts)
    if workdir not in ("", "."):
        inner = f"({line})" if shell else line
        line = f"(cd {shlex.quote(workdir)} && {inner})"
    if returncode is None:
        line += "  # not executed"
    elif returncode != 0:
        line += f"  # exit={returncode}"
    return line


def parse_resources(s: str) -> dict[str, int]:
    """Parse a resources string like 'cpu:4,gpu:1,memgb:16' into a dict.

    Raises
    ------
    StepUpError
        If a resource name is empty or a resource value is negative.
    """
    result = {}
    for item in s.split(","):
        item = item.strip()
        if not item:
            continue
        name, _, value = item.partition(":")
        name = name.strip()
        if not name:
            raise StepUpError(f"Resource name cannot be empty: {item}")
        if value == "":
            value = "1"
        value = int(value.strip())
        if value < 0:
            raise StepUpError(f"Resource value cannot be negative: {item}")
        result[name] = value
    return result


def merge_resources(base: str | None, override: str | None) -> str:
    """Merge two comma-separated resource specs; *override* wins per resource name."""
    merged = {**parse_resources(base or ""), **parse_resources(override or "")}
    return ",".join(f"{k}:{v}" for k, v in merged.items())


def positive_int(value):
    """Check if the argument is a positive integer (> 0)."""
    ivalue = int(value)
    if ivalue <= 0:
        raise ValueError(f"'{value}' is not strictly positive.")
    return ivalue


def is_process_running(pid: int) -> bool:
    """Whether a process with this pid exists (it may or may not be the director).

    Used together with `query_director_log` to tell a live director from a stale socket:
    by `get_socket` in `interact.py`, which keeps waiting for a director that is still
    starting up, and by the stale-socket check in `tui.py`, which refuses to start a
    build next to a director that may still be running.

    A pid can be recycled, so a `True` answer does not prove the process *is* the director.
    Both call sites are conservative on purpose: waiting or refusing is harmless,
    two directors on one database are not.
    """
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

    This is the single place that reads the header lines
    which `async_main` in `director.py` writes to `DIRECTOR_LOG`.
    It is used by `get_socket` in `interact.py`,
    which waits until a live director advertises its socket,
    and by the stale-socket check in `tui.py`,
    which refuses to start a build while a previous director's socket still exists.

    Parameters
    ----------
    director_log
        The path of the director log to read from.

    Returns
    -------
    socket_path
        The socket path advertised by the director, if it exists on disk, `None` otherwise.
    pid
        The pid advertised by the director,
        or `None` when the log holds no usable `PID` line.
    message
        An explanation of why no existing socket was found, empty when one was.
    """
    # The log is opened without testing for its existence first:
    # a director starting up in parallel wipes `.stepup/` before writing its own log,
    # so a file that exists when tested can be gone by the time it is opened.
    # Any error is reported as a message, i.e. the caller's retry path,
    # never as an exception escaping to the client's exit code.
    try:
        with open(director_log) as fh:
            line_socket = fh.readline()
            line_pid = fh.readline()
    except OSError as exc:
        return None, None, f"File {director_log} could not be read: {exc}"

    # A non-empty path is the only degenerate case worth guarding:
    # `async_main` writes each line in one shot (`sys.stderr` is line-buffered), so a short
    # but non-empty tail cannot occur in practice. Reading them from the same file handle can
    # at worst miss the `PID` line of a director that has just written the `SOCKET` line,
    # which the next attempt picks up.
    pid = None
    if line_pid.startswith("PID"):
        with contextlib.suppress(ValueError):
            pid = int(line_pid[3:])

    if not line_socket.startswith("SOCKET"):
        return None, pid, f"File {director_log} does not start with SOCKET line."
    socket_path = Path(line_socket[6:].strip())
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
a coroutine that was never awaited, a `Future` or `Task` collected while holding an
unhandled exception, a task destroyed while still pending, and an exception that escaped
a callback, a thread or a `__del__` (the unraisable hook prints `Exception ignored ...`).
None of these make the director exit with a non-zero return code,
which is precisely why the log must be scanned for them after the fact.
They are matched case-sensitively, since they are copied from CPython's sources.

The last pattern is of a different nature: it matches any log record at level `ERROR` or
`CRITICAL`, following the `format`/`datefmt` that `async_main` (`director.py`) hands to
`logging.basicConfig`. Matching the level *field* rather than the word anywhere in the line
is what keeps the director's own header lines out of it: `LOG_LEVEL ERROR` is not an error,
it is a build started with `--log-level=ERROR`.
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
        Empty when the director log is clean, i.e. the expected outcome of every build.
    """
    # As in `query_director_log`, the log is opened without testing for its existence first,
    # because a director starting up in parallel may remove it in between the two calls.
    findings = []
    try:
        with open(path_director_log) as fh:
            for line in fh:
                for pattern, label in DIRECTOR_LOG_CHECKS:
                    if pattern.search(line) is not None:
                        findings.append(f"{label}: {line.strip()}")
                        break
    except OSError:
        return []
    return findings


JOBLOG_COLUMNS = (
    "time_ns",
    "job_i",
    "event",
    "description",
)
"""Column names of the `--joblog` CSV file, in on-disk order."""


def reset_joblog(njob: int) -> None:
    """(Re)create `JOBLOG_CSV` and write its CSV header.

    Called once at the start of each build phase, discarding recordings of any previous phase.
    """
    row = (monotonic_ns(), 0, "INIT", f"maximum concurrent jobs: {njob}")
    with open(JOBLOG_CSV, "w", newline="") as fh:
        csv.writer(fh, quoting=csv.QUOTE_NONNUMERIC).writerow(JOBLOG_COLUMNS)
        csv.writer(fh, quoting=csv.QUOTE_NONNUMERIC).writerow(row)


def write_joblog_record(event: str, job_i: int, description: str) -> None:
    """Append one job-execution event to `JOBLOG_CSV` as a CSV row.

    This is the single place that fixes the row format, so every call site
    (in the scheduler and the executor) stays consistent.

    Parameters
    ----------
    event
        The kind of event, e.g. `"CREATED"`, `"STARTED"`, `"ENDED"`, `"COMPLETED"`.
    job_i
        The unique job identifier.
    description
        A human-readable description of the job or step, truncated to 100 characters.

    Notes
    -----
    The file is opened and closed for every call, so the write reaches disk synchronously and
    events stay correctly ordered even when jobs complete only milliseconds apart.
    Fields are quoted with `QUOTE_NONNUMERIC`, so `event` and `description` are always quoted
    (and thus unambiguous even if a description contains a comma or looks numeric),
    while the numeric columns stay bare.
    """
    row = (monotonic_ns(), job_i, event, description[:100])
    with open(JOBLOG_CSV, "a", newline="") as fh:
        csv.writer(fh, quoting=csv.QUOTE_NONNUMERIC).writerow(row)


def string_to_list(arg: Iterable[str] | str) -> list[str]:
    """Normalize a string or iterable of strings to a list of strings."""
    return [arg] if isinstance(arg, str) else list(arg)


def string_to_bool(v: str | bool) -> bool:
    """Convert a string to a boolean value, and return a boolean value unchanged.

    Parameters
    ----------
    v : str or bool
        The value to convert to a boolean.

    Returns
    -------
    bool
        The boolean representation of the input value.

    Raises
    ------
    StepUpError
        If the string cannot be interpreted as a boolean value.
    TypeError
        If the input is not a string or boolean.

    Examples
    --------
    >>> str2bool('yes')
    True
    >>> str2bool('no')
    False
    >>> str2bool(True)
    True
    >>> str2bool(False)
    False
    """
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        if v.lower() in ("yes", "true", "t", "y", "1"):
            return True
        if v.lower() in ("no", "false", "f", "n", "0"):
            return False
        raise StepUpError(f"Cannot interpret '{v}' as a boolean value.")
    raise TypeError(f"Expected a boolean value or string. Got {type(v).__name__}")


def is_debug() -> bool:
    """Whether `STEPUP_DEBUG` is switched on.

    The variable is read on every call, not cached, because a test may change it
    and because a step's environment is not the director's.
    """
    return string_to_bool(os.getenv("STEPUP_DEBUG", "0"))
