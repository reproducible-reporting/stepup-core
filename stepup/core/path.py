# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Specialized path operations"""

import os
from collections.abc import Iterable

from path import Path

from .exceptions import PathError

__all__ = (
    "StrPath",
    "apply_affixes",
    "coerce_path",
    "coerce_paths",
    "coerce_paths2",
    "coerce_str",
    "dir_range_upper",
    "get_affixes",
    "make_path_out",
    "translate",
    "translate_back",
)


# A path-like argument accepted by the user-facing API: either a `str`
# or any `os.PathLike` (such as a `pathlib.Path`).
StrPath = str | os.PathLike[str]

# All coercion functions wrap `os.fspath` to facilitate future refactorings of the code.


def coerce_str(arg: StrPath) -> str:
    """Convert a path-like argument via `os.fspath`."""
    return os.fspath(arg)


def coerce_path(arg: StrPath) -> Path:
    """Convert a path-like argument to a `path.Path` instance."""
    return Path(os.fspath(arg))


def coerce_paths(args: StrPath | Iterable[StrPath]) -> list[Path]:
    """Convert a path-like argument or flat iterable to `path.Path` instances."""
    if isinstance(args, (str, os.PathLike)):
        args = [args]
    return [Path(os.fspath(arg)) for arg in args]


def coerce_paths2(args: Iterable[StrPath | Iterable[StrPath]]) -> list[Path]:
    """Convert an iterable of paths or path sub-iterables, flattening one level of nesting."""
    result = []
    for arg in args:
        if isinstance(arg, (str, os.PathLike)):
            result.append(Path(os.fspath(arg)))
        else:
            result.extend(Path(os.fspath(a)) for a in arg)
    return result


def get_affixes(path: StrPath) -> tuple[str, str]:
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
    path = coerce_str(path)
    trailing = ""
    if path.endswith(os.sep):
        trailing = os.sep
        path = path[:-1]
    leading = f".{os.sep}" if path.startswith(f".{os.sep}") else ""
    return leading, trailing


def apply_affixes(path: StrPath, leading: str, trailing: str) -> Path:
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
    PathError
        If the path already has leading or trailing slashes and the corresponding affix is not None.
    PathError
        If the leading is given and not one of `""` or `"./"`.
    PathError
        If the trailing is given and not `""` or `"/"`.
    """
    path = coerce_str(path)
    if leading != "":
        if leading != f".{os.sep}":
            raise PathError(f"Leading affix must be one of '' or './', got '{leading}'")
        if path.startswith((os.sep, f".{os.sep}")):
            raise PathError(f"Path already has a leading slash: {path}")
        path = leading + path
    if trailing != "":
        if trailing != os.sep:
            raise PathError(f"Trailing affix must be '' or '/', got '{trailing}'")
        if path.endswith(os.sep):
            raise PathError(f"Path already has a trailing slash: {path}")
        path = path + trailing
    return coerce_path(path)


def make_path_out(
    path_in: StrPath, dest: StrPath | None, ext: str | None, other_exts: Iterable[str] = ()
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

    Raises
    ------
    PathError
        If the output path is equal to the input path,
        or if the output path does not have the expected extension.
    """
    path_in = coerce_path(path_in)
    if dest is not None:
        dest = coerce_str(dest)
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
        raise PathError(f"The output path cannot equal the input path: {path_out}")
    if not (ext is None or path_out.suffix == ext or path_out.suffix in other_exts):
        raise PathError(f"The output path does not have extension '{ext}': {path_out}.")
    return path_out


def translate(path: StrPath, workdir: StrPath = ".") -> Path:
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
    path = coerce_path(path).normpath()
    if not path.isabs():
        workdir = coerce_path(workdir).normpath()
        path = workdir / path
        if not workdir.isabs():
            root = Path(os.getenv("STEPUP_ROOT", os.getcwd()))
            here = Path(os.getenv("HERE", Path(".").relpath(root)))
            path = (root / here / path).normpath().relpath(root)
    return path


def translate_back(path: StrPath, workdir: StrPath = ".") -> Path:
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
    path = coerce_path(path).normpath()
    workdir = coerce_path(workdir).normpath()
    if path.isabs():
        if workdir.isabs() and path.startswith(workdir):
            path = Path(path).relpath(workdir)
    else:
        root = Path(os.getenv("STEPUP_ROOT", os.getcwd()))
        here = Path(os.getenv("HERE", Path(".").relpath(root)))
        path = Path(root / path).relpath(root / here / workdir)
    return path


def dir_range_upper(path: str) -> str:
    """Compute the exclusive upper bound of the label prefix range matched by a directory target.

    `path` always ends in `/` (`0x2F`). Since `0x2F + 1 == 0x30 == "0"`, replacing the
    trailing slash with `"0"` gives the smallest string that sorts (byte-wise) just past
    every label starting with `path`, without per-row string concatenation in SQL.
    """
    return path[:-1] + "0"
