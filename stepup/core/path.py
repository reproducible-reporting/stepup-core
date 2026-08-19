# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Specialized path operations."""

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
    "get_stepup_root",
    "make_path_out",
    "parent_dir",
    "short_path",
    "translate",
    "translate_back",
)


# A path-like argument accepted by the user-facing API:
# either a `str` or any `os.PathLike` (such as a `pathlib.Path`).
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
        The leading `./` of the path, or `""` if there is none.
    trailing
        The trailing slash of the path, or `""` if there is none.

    Notes
    -----
    For the special case of the path `"./"`, the leading is `""` and the trailing is `"/"`.
    """
    path = coerce_str(path)
    trailing = ""
    if path.endswith("/"):
        trailing = "/"
        path = path[:-1]
    leading = "./" if path.startswith("./") else ""
    return leading, trailing


def apply_affixes(path: StrPath, leading: str, trailing: str) -> Path:
    """Apply leading `./` and trailing `/` slashes to a path.

    Parameters
    ----------
    path
        The path to which the affixes will be applied.
    leading
        The leading `./` to apply or `""`.
    trailing
        The trailing slash to apply or `""`.

    Raises
    ------
    PathError
        If the path already has a leading or trailing slash
        and the corresponding affix is not empty.
    PathError
        If `leading` is neither `""` nor `"./"`.
    PathError
        If `trailing` is neither `""` nor `"/"`.
    """
    path = coerce_str(path)
    if leading != "":
        if leading != "./":
            raise PathError(f"Leading affix must be one of '' or './', got '{leading}'")
        if path.startswith(("/", "./")):
            raise PathError(f"Path already has a leading slash: {path}")
        path = leading + path
    if trailing != "":
        if trailing != "/":
            raise PathError(f"Trailing affix must be '' or '/', got '{trailing}'")
        if path.endswith("/"):
            raise PathError(f"Path already has a trailing slash: {path}")
        path = path + trailing
    return coerce_path(path)


def parent_dir(path: StrPath) -> str:
    """The directory containing `path`, with the project root written as `.`.

    A path ending in a separator names a directory,
    and its parent is then that same directory without the trailing separator.
    Strip the trailing separator before calling
    to get the directory one level up instead.

    Parameters
    ----------
    path
        A path relative to the project root.

    Returns
    -------
    parent
        The parent directory, never empty and without a trailing separator.
    """
    return str(coerce_path(path).parent) or "."


def make_path_out(
    path_in: StrPath, dest: StrPath | None, ext: str | None, other_exts: Iterable[str] = ()
) -> Path:
    """Construct an output path given the input path, a destination and the expected extension.

    Parameters
    ----------
    path_in
        The input path from which the output path can be derived.
    dest
        An output destination.
        Either `None` (only change extension),
        a destination directory (requires trailing slash) or a file.
        In all three cases, the output must have extension `ext`,
        unless `ext` is `None` or the extension is one of `other_exts`.
    ext
        The (new) extension of the output, e.g. `.pdf`.
        When `None`, the extension of the input is preserved.
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
    if dest is None or dest.endswith("/"):
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


def short_path(path: str | Path) -> Path:
    """Shorten a path for display in a message.

    A path inside the working directory is made relative to it, with a `./` prefix.
    Any other path is shown as it is:
    an absolute path is easier to read than a relative one climbing out of the tree,
    and a path like `~/.config/stepup.toml` already says where it is.
    """
    path = Path(path)
    if not path.startswith(os.getcwd() + os.sep):
        return path
    short = path.relpath()
    return short if short.startswith(".") else "./" / short


def get_stepup_root() -> Path:
    """Get the StepUp root directory.

    Returns
    -------
    stepup_root
        The StepUp root directory, which is either the value of `${STEPUP_ROOT}`,
        or the current working directory if the environment variable is not set.
        The returned path is absolute and normalized.
    """
    return Path(os.getenv("STEPUP_ROOT", os.getcwd())).absolute()


def translate(path: StrPath, workdir: StrPath = ".") -> Path:
    """Normalize the path and, if relative, make it relative to `STEPUP_ROOT`.

    Parameters
    ----------
    path
        The path to translate.
        If relative, it is assumed to be relative to `workdir`.
    workdir
        The working directory.
        If relative, it is assumed to be relative to `HERE`.

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
            root = get_stepup_root()
            here = Path(os.getenv("HERE", Path(".").relpath(root)))
            path = (root / here / path).normpath().relpath(root)
    return path


def translate_back(path: StrPath, workdir: StrPath = ".") -> Path:
    """If relative, make the path relative to `workdir`, assuming it is relative to `STEPUP_ROOT`.

    Parameters
    ----------
    path
        The path to translate.
        If relative, it is assumed to be relative to `STEPUP_ROOT`.
    workdir
        The working directory.
        If relative, it is assumed to be relative to `HERE`.

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
        root = get_stepup_root()
        here = Path(os.getenv("HERE", Path(".").relpath(root)))
        path = Path(root / path).relpath(root / here / workdir)
    return path


def dir_range_upper(parent: str) -> str:
    """Compute the exclusive upper bound for a range of paths that are all under the given parent.

    SQL queries can define the range of all paths under parent with the following WHERE clause:

    ```sql
    WHERE path >= :parent AND path < :upper_bound
    ```

    where `:parent` is the parent path and `:upper_bound` is the result of this function.

    Raises
    ------
    ValueError
        If `parent` does not end with a trailing slash.
    """
    # `parent` must end in `/` (`0x2F`).
    # Since `0x2F + 1 == 0x30 == "0"`,
    # replacing the trailing slash with `"0"` gives the smallest string
    # that sorts (byte-wise) just past every label starting with `parent`,
    # so a SQL query can use it as a bound parameter instead of concatenating strings per row.
    if not parent.endswith("/"):
        raise ValueError("Trailing slash expected to compute path range upper bound")
    return parent[:-1] + "0"
