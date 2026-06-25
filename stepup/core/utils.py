# StepUp Core provides the basic framework for the StepUp build tool.
# Copyright 2024-2026 Toon Verstraelen
#
# This file is part of StepUp Core.
#
# StepUp Core is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 3
# of the License, or (at your option) any later version.
#
# StepUp Core is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, see <http://www.gnu.org/licenses/>
#
# --
"""Small utilities used throughout."""

import asyncio
import logging
import re
import shlex
import sqlite3
import string
from collections.abc import Collection

import attrs
from path import Path

__all__ = (
    "CaseSensitiveTemplate",
    "DBLock",
    "format_command",
    "format_digest",
    "format_subprocess",
    "parse_resources",
    "string_to_bool",
    "string_to_list",
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
        raise ValueError(f"Executable is not a relative path: {executable}")
    relative = executable if executable.startswith(("./", "../")) else "." / executable
    return shlex.quote(relative)


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
    parts.append(cmd)
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
    """Parse a resources string like 'cpu:4,gpu:1,memgb:16' into a dict."""
    result = {}
    for item in s.split(","):
        item = item.strip()
        if not item:
            continue
        name, _, value = item.partition(":")
        name = name.strip()
        if not name:
            raise ValueError(f"Resource name cannot be empty: {item}")
        if value == "":
            value = "1"
        value = int(value.strip())
        if value < 0:
            raise ValueError(f"Resource value cannot be negative: {item}")
        result[name] = value
    return result


@attrs.define
class DBLock:
    """Exclusive asyncio lock for SQLite database access."""

    _con: sqlite3.Connection = attrs.field()
    _lock: asyncio.Lock = attrs.field(factory=asyncio.Lock)

    async def __aenter__(self):
        await self._lock.acquire()

    async def __aexit__(self, exc_type, exc, tb):
        if exc is None:
            self._con.commit()
        else:
            self._con.rollback()
        self._lock.release()


def string_to_list(arg: Collection[str] | str) -> list[str]:
    """Normalize a string or collection of strings to a list of strings."""
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
    ValueError
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
        raise ValueError(f"Cannot interpret '{v}' as a boolean value.")
    raise TypeError(f"Expected a boolean value or string. Got {type(v).__name__}")
