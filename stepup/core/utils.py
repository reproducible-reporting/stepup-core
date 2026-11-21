# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Small utilities used throughout."""

import decimal
import logging
import os
import re
import shlex
import stat
from collections.abc import Iterable
from decimal import Decimal

from .exceptions import NotARegularFileError, StepUpError
from .path import StrPath, coerce_str

__all__ = (
    "as_list",
    "escape_control_chars",
    "extract_env_overrides",
    "format_subprocess",
    "is_debug",
    "merge_resources",
    "open_regular_file",
    "parse_resources",
    "positive_decimal",
    "positive_int",
    "to_bool",
    "truncate_output",
)

logger = logging.getLogger(__name__)


# Matches a single leading `NAME=value` assignment in a command string,
# anchored at the scan position.
# The value may be unquoted, single-quoted, or double-quoted (shell-style).
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
    so nothing is extracted from `./cmd FOO=bar`.
    Values may be unquoted, single-quoted, or double-quoted, consistent with shell quoting.

    Parameters
    ----------
    command
        The raw command string, possibly prefixed with `VAR=value` assignments.

    Returns
    -------
    env_overrides
        A dictionary with the extracted environment variable overrides,
        or `None` when the command has no leading assignments.
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


def escape_control_chars(text: str) -> str:
    """Rewrite the control characters in a shell command or step label as `$'...'`-quoted escapes.

    An embedded control character (e.g. a literal newline)
    is spliced in as a `$'\\n'`-style ANSI-C-quoted escape,
    closing and reopening whichever quote is currently open around it.
    This keeps the result on a single line and,
    because adjacent shell tokens with no separator are concatenated,
    copy-pasting it into a POSIX shell reproduces `text` byte for byte.

    Parameters
    ----------
    text
        A shell command line, or a step label that reads as one.

    Returns
    -------
    escaped
        A single-line, shell-pasteable version of `text`.

    Notes
    -----
    This is a single-pass scanner that only tracks top-level single/double-quote nesting
    and backslash escaping.
    It does not recurse into nested `$(...)` or backtick command substitutions,
    so a control character embedded inside such a substitution's own quotes
    may be spliced incorrectly.
    This only affects how the text is displayed.
    """
    pieces = []
    quote = None  # None, "'", or '"'
    escaped = False
    for char in text:
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
    workdir: StrPath,
    *,
    env: dict[str, str] | None = None,
    returncode: int | None = None,
    shell: bool = False,
) -> str:
    """Format a recorded subprocess invocation as a single, shell-pasteable line.

    The result is informative, not authoritative:
    the command line is shown verbatim (the wrapper that recorded it is responsible for quoting),
    an environment overlay becomes a `VAR=value` assignment prefix,
    a non-default working directory is rendered with a `(cd ... && ...)` shell wrapper,
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
        The exit code of the subprocess,
        or `None` when the subprocess was not run or its exit code is not of interest.
    shell
        Whether `cmd` is a shell command line (as in `subprocess.run(..., shell=True)`).
        When `True`, a `cd` wrapper groups `cmd` in an extra `(...)`,
        so a compound command stays gated on the `cd` succeeding;
        a single command needs no such grouping.

    Returns
    -------
    line
        A single-line, shell-pasteable representation of the invocation.
    """
    parts = []
    if env:
        parts.extend(f"{key}={shlex.quote(value)}" for key, value in env.items())
    parts.append(escape_control_chars(cmd))
    line = " ".join(parts)
    workdir = coerce_str(workdir)
    if workdir not in ("", "."):
        inner = f"({line})" if shell else line
        line = f"(cd {shlex.quote(workdir)} && {inner})"
    if returncode is None:
        line += "  # not executed"
    elif returncode != 0:
        line += f"  # exit={returncode}"
    return line


def parse_resources(spec: str) -> dict[str, int]:
    """Parse a resources string like `cpu:4,gpu:1,memgb:16` into a dict.

    An item with no value (e.g. `cpu`) is interpreted as `cpu:1`.

    Raises
    ------
    StepUpError
        If a resource name is empty, a resource value is negative,
        or a resource value is not an integer.
    """
    result = {}
    for item in spec.split(","):
        item = item.strip()
        if not item:
            continue
        name, _, value = item.partition(":")
        name = name.strip()
        if not name:
            raise StepUpError(f"Resource name cannot be empty: {item}")
        if value == "":
            value = "1"
        try:
            value = int(value.strip())
        except ValueError as exc:
            raise StepUpError(f"Resource value is not a valid integer: {item}") from exc
        if value < 0:
            raise StepUpError(f"Resource value cannot be negative: {item}")
        result[name] = value
    return result


def merge_resources(base: str | None, override: str | None) -> str:
    """Merge two comma-separated resource specs. (`override` wins per resource name.)"""
    merged = {**parse_resources(base or ""), **parse_resources(override or "")}
    return ",".join(f"{k}:{v}" for k, v in merged.items())


_IRREGULAR_FILE_KINDS = (
    (stat.S_ISDIR, "a directory"),
    (stat.S_ISFIFO, "a FIFO"),
    (stat.S_ISCHR, "a character device"),
    (stat.S_ISBLK, "a block device"),
    (stat.S_ISSOCK, "a socket"),
)
"""The kinds of files that are not regular files, with their names in error messages."""


def _irregular_file_error(path: StrPath, st_mode: int) -> NotARegularFileError:
    """Create the exception for a file that is not a regular file, given its `st_mode`."""
    kind = next(
        (kind for test, kind in _IRREGULAR_FILE_KINDS if test(st_mode)),
        "a file of another kind",
    )
    return NotARegularFileError(f"Not a regular file but {kind}: {path}")


def _is_directory(path: StrPath) -> bool:
    """Return `True` when `path` is a directory, or a symbolic link to one."""
    try:
        return stat.S_ISDIR(os.stat(path).st_mode)
    except OSError:
        return False


def open_regular_file(path: StrPath, flags: int, mode: int = 0o777) -> int:
    """Open a file like `os.open`, but only when it is a regular file.

    A FIFO or a device is opened without waiting for the other side, and then refused.
    A directory is refused with the same exception on every platform.
    Use it as the `opener` of `open()`, which adds the flags that the platform needs,
    such as `O_BINARY` on Windows.

    Parameters
    ----------
    path, flags, mode
        As in `os.open`.

    Returns
    -------
    fd
        The file descriptor of the regular file, in blocking mode.

    Raises
    ------
    NotARegularFileError
        When the file is not a regular file.
    OSError
        When the file cannot be opened,
        e.g. a FIFO without a reader that is opened for writing fails with `ENXIO`.
    """
    # Windows has no `O_NONBLOCK`, and no FIFOs in the file system either.
    nonblocking = getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(path, flags | nonblocking, mode)
    except (PermissionError, IsADirectoryError) as exc:
        # Windows refuses to open a directory with `EACCES`,
        # and POSIX refuses to open one for writing with `EISDIR`.
        # The kind of file is only examined after the failure, never before opening it,
        # so a file that is replaced in the meantime can at worst change the exception.
        if _is_directory(path):
            raise _irregular_file_error(path, stat.S_IFDIR) from exc
        raise
    try:
        st_mode = os.fstat(fd).st_mode
        if not stat.S_ISREG(st_mode):
            raise _irregular_file_error(path, st_mode)
        if nonblocking:
            # Later reads and writes are ordinary ones, also on a FUSE file system,
            # which would pass the flag on to its daemon.
            os.set_blocking(fd, True)
    except BaseException:
        os.close(fd)
        raise
    return fd


def as_list(values: Iterable[str] | str) -> list[str]:
    """Normalize a single string or an iterable of strings to a list of strings."""
    return [values] if isinstance(values, str) else list(values)


def to_bool(value: str | bool) -> bool:
    """Convert a string to a boolean value, passing a boolean through unchanged.

    Parameters
    ----------
    value
        The value to convert to a boolean.

    Returns
    -------
    flag
        The boolean representation of the input value.

    Raises
    ------
    StepUpError
        If the string cannot be interpreted as a boolean value.
    TypeError
        If the input is neither a string nor a boolean.
        A configuration file can hold any TOML type here,
        so this guard names the mistake instead of failing on a missing string method.

    Examples
    --------
    >>> to_bool('yes')
    True
    >>> to_bool('no')
    False
    >>> to_bool(True)
    True
    >>> to_bool(False)
    False
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        if value.lower() in ("yes", "true", "t", "y", "1"):
            return True
        if value.lower() in ("no", "false", "f", "n", "0"):
            return False
        raise StepUpError(f"Cannot interpret '{value}' as a boolean value.")
    raise TypeError(f"Expected a boolean value or string. Got {type(value).__name__}")


def is_debug() -> bool:
    """Whether `STEPUP_DEBUG` is switched on.

    The variable is read on every call, not cached, because a test may change it
    and because a step's environment is not the director's.
    """
    return to_bool(os.getenv("STEPUP_DEBUG", "0"))


def truncate_output(content: str, max_bytes: int | None = None) -> str:
    """Truncate `content` to at most `max_bytes` UTF-8 bytes, appending a sentinel if cut.

    A `max_bytes` of `0` (or any non-positive value) means unlimited: the content is
    returned unchanged. Otherwise the cut is made on a valid UTF-8 character boundary
    (`decode(..., "ignore")` drops a trailing partial multi-byte sequence), so the result
    is always valid text.

    Parameters
    ----------
    content
        The captured text to (possibly) truncate.
    max_bytes
        Maximum number of UTF-8 bytes to keep, or `0` (or any non-positive value) for
        unlimited.
        The default `None` takes the budget from `${STEPUP_MAX_OUTPUT_SIZE}`,
        which is unlimited when unset.

    Returns
    -------
    truncated
        The original content if within the budget or unlimited, otherwise the content cut
        to `max_bytes` bytes with a sentinel line appended.
    """
    if max_bytes is None:
        # The variable is read on every call, not cached,
        # because a test may change it and because a step's environment is not the director's.
        max_bytes = int(os.getenv("STEPUP_MAX_OUTPUT_SIZE", "0"))
    if max_bytes <= 0:
        return content
    encoded = content.encode("utf-8")
    if len(encoded) <= max_bytes:
        return content
    truncated = encoded[:max_bytes].decode("utf-8", "ignore")
    return f"{truncated}\n[output truncated at {max_bytes} bytes]\n"


def positive_int(value: str | int) -> int:
    """Convert a command-line or config value to an integer, requiring it to be strictly positive.

    Raises
    ------
    ValueError
        If the value is not an integer or is not strictly positive.
    """
    number = int(value)
    if number <= 0:
        raise ValueError(f"'{value}' is not strictly positive.")
    return number


def positive_decimal(value: str | float) -> Decimal:
    """Convert a command-line or config value to a strictly positive `Decimal`.

    The value is converted through its string form,
    so that the number of digits after the decimal point is preserved:
    `Decimal(3.0)` is `Decimal("3")` while `Decimal("3.0")` keeps the digit,
    and a setting may give that digit a meaning of its own.
    A config file may hold a TOML float here, unlike the command line, which is always a string.

    Raises
    ------
    ValueError
        If the value is not a number or is not strictly positive.
    """
    try:
        number = Decimal(str(value))
    except decimal.InvalidOperation as exc:
        raise ValueError(f"not a number: {value!r}") from exc
    if number <= 0:
        raise ValueError(f"must be strictly positive: {value!r}")
    return number
