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
import os
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
    "check_plan",
    "format_command",
    "format_digest",
    "format_subprocess",
    "make_path_out",
    "myabsolute",
    "mynormpath",
    "myparent",
    "myrealpath",
    "myrelpath",
    "parse_resources",
    "remove_path",
    "string_to_bool",
    "string_to_list",
    "translate",
    "translate_back",
)


logger = logging.getLogger(__name__)


#
# Custom path operations
#


def _myfix_trailing(path: str, result: Path, enforce: bool = False) -> Path:
    if (path.endswith(os.sep) or enforce) and not result.endswith("/"):
        result = result / ""
    return result


def _myfix_leading(path: str, result: Path) -> Path:
    if path.startswith(f".{os.sep}") and not result.startswith("./"):
        result = "." / result
    return result


def mynormpath(path: str) -> Path:
    """Normalize the path but keep the leading and trailing separator"""
    return _myfix_leading(path, _myfix_trailing(path, Path(path).normpath()))


def myrealpath(path: str) -> Path:
    """Like Path.realpath path but keep the trailing separator"""
    return _myfix_leading(path, _myfix_trailing(path, Path(path).realpath()))


def myrelpath(path: str, start: str = ".") -> Path:
    """Like Path.relpath path but keep the trailing separator"""
    return _myfix_leading(path, _myfix_trailing(path, Path(path).relpath(start)))


def myabsolute(path: str, is_dir: bool = False) -> Path:
    """Like Path.absolute path but keep the trailing separator"""
    return _myfix_trailing(path, Path(path).absolute(), enforce=is_dir)


def myparent(path: str) -> Path | None:
    """Construct the parent directory.

    Parameters
    ----------
    path
        The path of which the parent must be constructed.
        The path is first normalized, so trailing slashes are removed.

    Returns
    -------
    parent
        Parent path with trailing slash, to clarify that it is a directory.
        None when the given path is "." or "/".
    """
    path = Path(path).normpath()
    if path in "./":
        return None
    result = path.parent
    if result == "":
        result = Path(".")
    if not result.endswith(os.sep):
        result = result / ""
    return result


def make_path_out(
    path_in: str, dest: str | None, ext: str | None, other_exts: Collection[str] = ()
) -> Path:
    """Construct an output path given the input path, an out argument and the expected extension.

    Parameters
    ----------
    path_in
        The input path from which the output path can be derived.
    dest
        An output destination.
        Either None (only change extension),
        a destination directory (requires trailing slash) or a file.
        In either case, the extension of the output is equal to ext.
    ext
        The (new) extension of the output, e.g. .pdf.
        When None, the extension of the input is preserved.
    other_exts
        Other extensions that are allowed for the output.

    Returns
    -------
    path_out
        A properly formatted output path.
    """
    path_in = Path(path_in)
    if dest is None or dest.endswith(os.sep):
        path_out = path_in
        if ext is not None:
            path_out = Path(path_out.stem + ext)
        if dest is None:
            path_out = path_in.parent / path_out
        else:
            path_out = path_out.basename()
            if dest not in (".", "./"):
                path_out = Path(dest) / path_out
    else:
        path_out = Path(dest)
    if path_out == path_in:
        raise ValueError(f"The output path cannot equal the input path: {path_out}")
    if not (ext is None or path_out.suffix == ext or path_out.suffix in other_exts):
        raise ValueError(f"The output path does not have extension '{ext}': {path_out}.")
    return path_out


def remove_path(path: str) -> bool:
    """Remove a file or directory. Return `True` of the file was removed."""
    path = Path(path)
    if path.endswith("/"):
        try:
            path.rmdir()
            return True
        except FileNotFoundError:
            return False
        except OSError:
            return False
    else:
        try:
            path.remove()
            return True
        except FileNotFoundError:
            return False
        except OSError:
            return False


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


def check_plan(path_plan: str):
    """Basic sanity checks for a plan.py file."""
    if not Path(path_plan).is_file():
        raise ValueError(f"Is not a file: {path_plan}")
    if not os.access(path_plan, os.X_OK):
        raise ValueError(f"File is not executable: {path_plan}")
    with open(path_plan) as fh:
        shebang = "#!/usr/bin/env python3"
        if not fh.readline().rstrip() == shebang:
            raise ValueError(f"First line of plan differs from '{shebang}': {path_plan}")


def format_digest(digest: bytes) -> str:
    hexdigest = digest.hex()
    return " ".join(hexdigest[i : i + 8] for i in range(0, 64, 8))


def format_command(executable: str) -> str:
    """Format a relative path to a local executable for execution in a shell."""
    if executable.startswith("/"):
        raise ValueError(f"Executable is not a relative path: {executable}")
    return shlex.quote(executable if executable.startswith(("./", "../")) else f"./{executable}")


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
    if workdir not in ("./", "", "."):
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


def translate(path: str, workdir: str = ".") -> Path:
    """Normalize the path and, if relative, make it relative to `self.root`.

    Parameters
    ----------
    path
        The path to translate. If relative, it assumed to be relative to the working directory.
    workdir
        The work directory. If relative, it is assumed to be relative to `self.here`

    Returns
    -------
    translated_path
        A path that can be interpreted in the working directory of the StepUp director.
    """
    path = mynormpath(path)
    if not path.isabs():
        workdir = mynormpath(workdir)
        path = workdir / path
        if not workdir.isabs():
            root = Path(os.getenv("STEPUP_ROOT", os.getcwd()))
            here = Path(os.getenv("HERE", myrelpath("./", root)))
            path = myrelpath(mynormpath(root / here / path), root)
    return path


def translate_back(path: str, workdir: str = ".") -> Path:
    """If relative, make it relative to work directory, assuming it is relative to `self.root`.

    Parameters
    ----------
    path
        The path to translate. If relative, it is assumed to be relative to `ROOT`.
    workdir
        The working directory. If relative, it is assumed to be relative to `HERE`.

    Returns
    -------
    back_translated_path
        A path that can be interpreted in the working directory.
    """
    path = mynormpath(path)
    workdir = mynormpath(workdir)
    if path.isabs():
        if workdir.isabs() and path.startswith(workdir):
            path = myrelpath(path, workdir)
    else:
        root = Path(os.getenv("STEPUP_ROOT", os.getcwd()))
        here = Path(os.getenv("HERE", myrelpath("./", root)))
        path = myrelpath(root / path, root / here / workdir)
    return path


def string_to_list(arg: Collection[str] | str) -> list[str]:
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
