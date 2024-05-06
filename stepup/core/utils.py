# StepUp Core provides the basic framework for the StepUp build tool.
# Copyright (C) 2024 Toon Verstraelen
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

import importlib.util
import os
import re
import string
from types import ModuleType

from path import Path

__all__ = (
    # Path manipulation
    "mynormpath",
    "myrelpath",
    "myabsolute",
    "myparent",
    "make_path_out",
    "remove_path",
    # Miscellaneous
    "classproperty",
    "lookupdict",
    "CaseSensitiveTemplate",
    "check_plan",
    "check_inp_path",
    "format_digest",
    "load_module_file",
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


def make_path_out(path_in: str, out: str | None, ext: str | None) -> Path:
    """Construct an output path given the input path, an out argument and the expected extension.

    Parameters
    ----------
    path_in
        The input path from which the output path can be derived.
    out
        An output path argument.
        Either None (only change extension),
        a destination directory (requires trailing slash) or a file.
        In either case, the extension of the output is equal to ext.
    ext
        The (new) extension of the output, e.g. .pdf.
        When None, the extension of the input is preserved.

    Returns
    -------
    path_out
        A properly formatted output path.
    """
    path_in = Path(path_in)
    if out is None or out.endswith(os.sep):
        path_out = path_in
        if ext is not None:
            path_out = Path(path_out.stem + ext)
        path_out = path_in.parent / path_out if out is None else Path(out) / path_out.basename()
    else:
        path_out = Path(out)
    if path_out == path_in:
        raise ValueError(f"The output path cannot equal the input path: {path_out}")
    if not (ext is None or path_out.suffix == ext):
        raise ValueError(f"The output path does not have extension '{ext}': {path_out}.")
    return path_out


def remove_path(path: Path) -> bool:
    """Remove a file or directory. Return `True` of the file was removed."""
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


# Adapted from https://stackoverflow.com/a/39542816/494584
class classproperty(property):
    def __get__(self, obj, objtype=None):
        return super().__get__(objtype)

    def __set__(self, obj, value):
        super().__set__(type(obj), value)

    def __delete__(self, obj):
        super().__delete__(type(obj))


class lookupdict(dict):
    """Dictionary assigning enumerated values to keys."""

    def __missing__(self, key):
        if not isinstance(key, str):
            raise TypeError("lookupdict only supports string keys")
        size = len(self)
        self[str(key)] = size
        return size

    def get_list(self):
        """Return all items in the lookup dictionary as a list."""
        result = [None] * len(self)
        for key, idx in self.items():
            result[idx] = key
        return result


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
        shebang = "#!/usr/bin/env python"
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


def load_module_file(path_py: str, name: str = "pythonscript") -> ModuleType:
    """Load a Python module from a given path."""
    spec = importlib.util.spec_from_file_location(f"<{name}>", str(path_py))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
