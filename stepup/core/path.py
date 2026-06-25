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
"""Specialized path operations"""

import os
from collections.abc import Collection

from path import Path

__all__ = ("apply_affixes", "get_affixes", "make_path_out", "translate", "translate_back")


def get_affixes(path: str) -> tuple[str, str]:
    """Get the leading `./` and trailing `/` of a path.

    Parameters
    ----------
    path
        The path from which the affixes will be extracted.

    Returns
    -------
    leading
        The leading slash of the path, or `""` if there is none.
    trailing
        The trailing slash of the path, or `""` if there is none.

    Notes
    -----
    For the special case of the path `"./"`, the leading is `""` and the trailing is `"/"`.
    """
    trailing = ""
    if path.endswith(os.sep):
        trailing = os.sep
        path = path[:-1]
    leading = f".{os.sep}" if path.startswith(f".{os.sep}") else ""
    return leading, trailing


def apply_affixes(path: str, leading: str, trailing: str) -> str:
    """Apply leading `./` and trailing `/` slashes to a path.

    Parameters
    ----------
    path
        The path to which the affixes will be applied.
    leading
        The leading slash to apply or `""`.
    trailing
        The trailing slash to apply or `""`.

    Raises
    ------
    ValueError
        If the path already has leading or trailing slashes and the corresponding affix is not None.
    ValueError
        If the leading is given and not one of `""` or `"./"`.
    ValueError
        If the trailing is given and not `""` or `"/"`.
    """
    if leading != "":
        if leading != f".{os.sep}":
            raise ValueError(f"Leading affix must be one of '' or './', got '{leading}'")
        if path.startswith((os.sep, f".{os.sep}")):
            raise ValueError(f"Path already has a leading slash: {path}")
        path = leading + path
    if trailing != "":
        if trailing != os.sep:
            raise ValueError(f"Trailing affix must be '' or '/', got '{trailing}'")
        if path.endswith(os.sep):
            raise ValueError(f"Path already has a trailing slash: {path}")
        path = path + trailing
    return path


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
    path = Path(path).normpath()
    if not path.isabs():
        workdir = Path(workdir).normpath()
        path = workdir / path
        if not workdir.isabs():
            root = Path(os.getenv("STEPUP_ROOT", os.getcwd()))
            here = Path(os.getenv("HERE", Path(".").relpath(root)))
            path = (root / here / path).normpath().relpath(root)
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
    path = Path(path).normpath()
    workdir = Path(workdir).normpath()
    if path.isabs():
        if workdir.isabs() and path.startswith(workdir):
            path = Path(path).relpath(workdir)
    else:
        root = Path(os.getenv("STEPUP_ROOT", os.getcwd()))
        here = Path(os.getenv("HERE", Path(".").relpath(root)))
        path = Path(root / path).relpath(root / here / workdir)
    return path
