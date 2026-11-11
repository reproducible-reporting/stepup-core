# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Specialized path operations."""

import functools
import ntpath
import os
import shlex
import sys
import types
from collections.abc import Callable, Iterable
from typing import Any, Self

from path import Path as BasePath

from .exceptions import PathError

__all__ = (
    "Path",
    "StrPath",
    "apply_affixes",
    "coerce_path",
    "coerce_paths",
    "coerce_paths2",
    "coerce_str",
    "dir_range_upper",
    "format_local_executable",
    "get_affixes",
    "get_here",
    "get_root",
    "get_stepup_root",
    "is_path_type",
    "make_path_out",
    "parent_dir",
    "short_path",
    "to_slash",
    "translate",
    "translate_back",
)


# A path-like argument accepted by the user-facing API:
# either a `str` or any `os.PathLike` (such as a `pathlib.Path`).
StrPath = str | os.PathLike[str]


def _nt_to_slash(path: str) -> str:
    """Write a Windows path with `/` separators and an upper-case drive letter.

    The drive letter is only changed when it is followed by a separator or by nothing,
    because a colon after a one-letter file name may also start the name of a data stream.
    """
    # The unbound `str.replace` returns an exact `str`, also for an instance of a subclass.
    path = str.replace(path, "\\", "/")
    if path[1:2] == ":" and path[2:3] in ("/", "") and "a" <= path[0] <= "z":
        path = path[0].upper() + path[1:]
    return path


def to_slash(path: str) -> str:
    """Convert a path string received from the operating system to StepUp's spelling.

    Inside StepUp, `/` is the only path separator on every platform.
    On Windows, backslashes are replaced by `/` and the drive letter is written in upper case,
    because the operating system does not distinguish these spellings,
    while StepUp compares paths as strings.
    On other platforms, a backslash is an ordinary file name character
    and the path is returned unchanged.

    Use this function for plain strings.
    The constructor of [`Path`][stepup.core.path.Path] performs the same conversion.
    """
    if sys.platform == "win32":
        return _nt_to_slash(path)
    return path


def _plain(arg: Any) -> Any:
    """Convert a path argument to an exact `str`, and return any other argument unchanged.

    `os.fspath()` returns an instance of a `str` subclass as it is.
    Pure-Python functions of `ntpath` call `str` methods on their arguments,
    such as `replace`, which path classes may override.
    """
    if isinstance(arg, (str, os.PathLike)):
        arg = os.fspath(arg)
        return str(arg) if isinstance(arg, str) else arg
    if isinstance(arg, (list, tuple)):
        return [_plain(item) for item in arg]
    return arg


def _nt_to_slash_all(results: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(_nt_to_slash(result) for result in results)


def _wrap_nt(func: Callable, convert: Callable | None) -> Callable:
    """Wrap a function of `ntpath` to take plain strings and, optionally, convert its result."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        result = func(
            *[_plain(arg) for arg in args],
            **{key: _plain(value) for key, value in kwargs.items()},
        )
        return result if convert is None else convert(result)

    return wrapper


def _nt_isabs(path: StrPath) -> bool:
    """Test whether a Windows path has a root, with or without a drive.

    `ntpath.isabs("/dir")` is true up to Python 3.12 and false in later versions,
    because such a path is relative to the current drive.
    StepUp treats it as absolute in all versions:
    the path does not depend on the current directory,
    which is what the callers of this function need to know.
    """
    path = _nt_to_slash(os.fspath(path))
    return path.startswith("/") or path[1:3] == ":/"


# Functions of `ntpath` that return a path, or a tuple of parts of a path.
_NT_STR_FUNCS = frozenset(
    {
        "abspath",
        "basename",
        "commonpath",
        "commonprefix",
        "dirname",
        "expanduser",
        "expandvars",
        "join",
        "normcase",
        "normpath",
        "realpath",
        "relpath",
    }
)
_NT_TUPLE_FUNCS = frozenset({"split", "splitdrive", "splitext", "splitroot"})


def _make_nt_slash_module() -> types.ModuleType:
    """Create a stand-in for `ntpath` whose functions return paths in StepUp's spelling.

    The results get the same conversion as the argument of the `_NtPath` constructor.
    Methods of `path.Path` compare results of these functions to parts of instances,
    which only works when both are spelled the same way.

    The set of functions follows `ntpath.__all__`,
    because some of them only exist in recent Python versions.
    """
    module = types.ModuleType("ntslashpath", "A variant of ntpath that returns forward slashes.")
    for name in ntpath.__all__:
        obj = getattr(ntpath, name)
        if callable(obj):
            if name in _NT_STR_FUNCS:
                obj = _wrap_nt(obj, _nt_to_slash)
            elif name in _NT_TUPLE_FUNCS:
                obj = _wrap_nt(obj, _nt_to_slash_all)
            else:
                obj = _wrap_nt(obj, None)
        setattr(module, name, obj)
    module.isabs = _nt_isabs
    module.sep = "/"
    module.altsep = "\\"
    return module


class _NtPath(BasePath):
    """The Windows flavor of `Path`.

    It only uses string operations of `ntpath`, which is importable on every platform,
    so that this class can be instantiated and tested everywhere.
    """

    module = _make_nt_slash_module()

    def __new__(cls, other: Any = ".") -> Self:
        return str.__new__(cls, _nt_to_slash(os.fspath(other)))

    def replace(self, target_or_old: str, *args) -> Self:
        """Rename the file (one argument) or substitute substrings (more arguments).

        The result of a substitution is not normalized,
        so that it is the same string as `str.replace` would return.
        """
        if args:
            return str.__new__(type(self), str.replace(self, target_or_old, *args))
        return super().replace(target_or_old)


class Path(_NtPath if sys.platform == "win32" else BasePath):
    """A file system path with `/` as its only separator, on every platform.

    This is a subclass of `path.Path` from the [path](https://pypi.org/project/path/) library,
    and therefore also of `str`.
    It preserves a leading `./` and a trailing `/`, which carry meaning in StepUp.

    On POSIX systems, it behaves exactly like `path.Path`.
    On Windows, the constructor replaces backslashes by `/`
    and writes the drive letter in upper case,
    and all methods return paths with `/` separators, e.g. `C:/Users/me/project/plan.py`.
    Windows accepts such paths in all its file system functions.
    Use `os.path.normpath()` to obtain the native spelling when another program requires it.
    """


def is_path_type(cls: Any) -> bool:
    """Test whether `cls` is the `Path` class of StepUp or of the `path` library, or a subclass.

    Use this function to recognize path objects created by a user,
    who may have imported either class.
    """
    return isinstance(cls, type) and issubclass(cls, BasePath)


def coerce_str(arg: StrPath) -> str:
    """Convert a path-like argument to a string in StepUp's spelling.

    See [`to_slash()`][stepup.core.path.to_slash] for the conversion on Windows.
    """
    return to_slash(os.fspath(arg))


def coerce_path(arg: StrPath) -> Path:
    """Convert a path-like argument to a `Path` instance."""
    return Path(os.fspath(arg))


def coerce_paths(args: StrPath | Iterable[StrPath]) -> list[Path]:
    """Convert a path-like argument or flat iterable to `Path` instances."""
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
    if not path.startswith(Path.cwd() + "/"):
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


def get_here(workdir: StrPath = ".") -> Path:
    """Get the relative path from the StepUp root to a working directory.

    This is the Python counterpart of the environment variable `HERE`,
    which is not consulted, so the result is also correct in a process
    that changed its working directory without updating `HERE`.
    See [HERE and ROOT](../advanced_topics/here_and_root.md) for more details.

    Parameters
    ----------
    workdir
        The working directory, relative to the current working directory.
        The default is the current working directory itself.

    Returns
    -------
    here
        The relative path from the StepUp root to `workdir`.
        Both directories are physical paths, with all symbolic links resolved.
        The path starts with `../` when `workdir` lies outside the StepUp root.
    """
    return coerce_path(workdir).realpath().relpath(_get_real_root())


def get_root(workdir: StrPath = ".") -> Path:
    """Get the relative path from a working directory to the StepUp root.

    This is the Python counterpart of the environment variable `ROOT`,
    which is not consulted, so the result is also correct in a process
    that changed its working directory without updating `ROOT`.
    See [HERE and ROOT](../advanced_topics/here_and_root.md) for more details.

    Parameters
    ----------
    workdir
        The working directory, relative to the current working directory.
        The default is the current working directory itself.

    Returns
    -------
    root
        The relative path from `workdir` to the StepUp root.
        Both directories are physical paths, with all symbolic links resolved.
    """
    return _get_real_root().relpath(coerce_path(workdir).realpath())


def _get_real_root() -> Path:
    """Get the physical StepUp root, with all symbolic links resolved."""
    return _realpath_cached(get_stepup_root())


@functools.lru_cache(maxsize=16)
def _realpath_cached(path: Path) -> Path:
    """Resolve symbolic links, with caching because API functions may translate many paths."""
    return path.realpath()


def _anchor(path: Path) -> Path:
    """Prepare a path for the computation of a relative path to or from the real root.

    A relative path is interpreted in the current directory, which POSIX reports as a physical path.
    Windows reports the path through which the directory was entered, symbolic links included,
    so there a relative path is joined to the resolved current directory first.
    """
    if sys.platform == "win32" and not path.isabs():
        return _realpath_cached(Path.cwd()) / path
    return path


def translate(path: StrPath, workdir: StrPath = ".") -> Path:
    """Normalize the path and, if relative, make it relative to `STEPUP_ROOT`.

    Parameters
    ----------
    path
        The path to translate.
        If relative, it is assumed to be relative to `workdir`.
    workdir
        The working directory.
        If relative, it is assumed to be relative to the current working directory.

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
            path = _anchor(path).relpath(_get_real_root())
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
        If relative, it is assumed to be relative to the current working directory.

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
        path = (_get_real_root() / path).relpath(_anchor(workdir))
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


def format_local_executable(executable: StrPath) -> str:
    """Format a relative path to a local executable for execution in a shell.

    A path without a leading `./` or `../` gets a `./` prefix,
    so that a shell runs the file at that path instead of searching `PATH` for its name.

    Raises
    ------
    PathError
        If `executable` is an absolute path.
    """
    executable = coerce_path(executable)
    if executable.isabs():
        raise PathError(f"Executable is not a relative path: {executable}")
    relative = executable if executable.startswith(("./", "../")) else "." / executable
    return shlex.quote(relative)
