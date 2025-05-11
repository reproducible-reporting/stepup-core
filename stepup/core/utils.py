# StepUp Core provides the basic framework for the StepUp build tool.
# © 2024–2025 Toon Verstraelen
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
import os
import re
import shlex
import sqlite3
import string
import sys
from collections.abc import Collection

import attrs
from path import Path

__all__ = (
    "CaseSensitiveTemplate",
    "DBLock",
    "check_inp_path",
    "check_plan",
    "format_command",
    "format_digest",
    "get_local_import_paths",
    "make_path_out",
    "myabsolute",
    "mynormpath",
    "myparent",
    "myrelpath",
    "remove_path",
    "string_to_bool",
    "string_to_list",
    "translate",
    "translate_back",
)


#
# Custom path operations
#


def mynormpath(path: str) -> Path:
    """Normalize the path but keep the trailing separator"""
    result = Path(path).normpath()
    if path.endswith(os.sep) and not result.endswith("/"):
        result = result / ""
    return result


def myrelpath(path: str, start: str = ".") -> Path:
    """Like Path.relpath path but keep the trailing separator"""
    result = Path(path).relpath(start)
    if path.endswith(os.sep) and not result.endswith("/"):
        result = result / ""
    return result


def myabsolute(path: str, is_dir: bool = False) -> Path:
    """Like Path.absolute path but keep the trailing separator"""
    result = Path(path).absolute()
    if (path.endswith(os.sep) or is_dir) and not result.endswith("/"):
        result = result / ""
    return result


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
        path_out = path_in.parent / path_out if dest is None else Path(dest) / path_out.basename()
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


def check_inp_path(inp_path: Path) -> str | None:
    if inp_path.is_dir():
        if not inp_path.endswith("/"):
            return "Directory without trailing separator"
    elif inp_path.exists():
        if inp_path.endswith("/"):
            return "Path is not a directory"
    else:
        return "Path does not exist"


def format_digest(digest: bytes) -> tuple[str, str]:
    hexdigest = digest.hex()
    return (
        " ".join(hexdigest[i : i + 8] for i in range(0, 64, 8)),
        " ".join(hexdigest[i : i + 8] for i in range(64, 128, 8)),
    )


def format_command(executable: str) -> str:
    """Format a relative path to a local executable for execution in a shell."""
    if executable.startswith("/"):
        raise ValueError(f"Executable is not a relative path: {executable}")
    return shlex.quote(executable if executable.startswith(("./", "../")) else f"./{executable}")


def filter_dependencies(paths: Collection[str]) -> set[Path]:
    """Select path retained by the `${STEPUP_PATH_FILTER}`.

    Parameters
    ----------
    paths
        A collection of paths to filter.
        Relative paths are assumed to be relative to the current working directory.

    Returns
    -------
    filtered_paths
        A collection of paths relative to `${STEPUP_ROOT}` that were retained by the filter.
    """
    # Parse the ${STEPUP_PATH_FILTER} environment variable.
    filter_str = os.environ.get("STEPUP_PATH_FILTER", "-venv")
    filter_str += ":+.:-/"
    rules = []
    stepup_root = Path(os.getenv("STEPUP_ROOT", os.getcwd()))
    for filter_item in filter_str.split(":"):
        if filter_item == "":
            continue
        if filter_item.startswith("+"):
            keep = True
        elif filter_item.startswith("-"):
            keep = False
        else:
            raise ValueError(f"Invalid filter item: {filter_item}")
        prefix = filter_item[1:]
        if not prefix.startswith("/"):
            prefix = mynormpath(stepup_root / prefix)
        rules.append((prefix, keep))

    # Filter paths according to the rules.
    result = set()
    for path in paths:
        abspath = myabsolute(path)
        for prefix, keep in rules:
            if abspath.startswith(prefix):
                if keep:
                    result.add(myrelpath(path))
                break
        else:
            raise AssertionError("No matching rule found for path: {path}")
    return result


def get_local_import_paths(script_path: Path | None = None) -> list[str]:
    """Get all local files from `sys.modules`.

    Files are only included if they match the `${STEPUP_PATH_FILTER}` environment variable.
    """

    def iter_module_paths():
        for module in sys.modules.values():
            mod_path = getattr(module, "__file__", None)
            if not (mod_path is None or mod_path.startswith("<")):
                yield mod_path

    mod_paths = filter_dependencies(iter_module_paths())
    # The script path is already included in the inputs.
    if script_path is not None:
        mod_paths.discard(script_path)
    return sorted(mod_paths)


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
