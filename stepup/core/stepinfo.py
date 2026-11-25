# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Information about a step in a StepUp build, intended for defining follow-up steps."""

import json
from collections.abc import Iterable

import attrs
from cattrs.gen import make_dict_unstructure_fn

from .cattrs import json_converter
from .exceptions import StepUpError
from .nglob import NamedGlob
from .path import (
    Path,
    StrPath,
    anchor_path,
    coerce_path,
    parent_dir,
    translate,
    translate_back,
)

__all__ = ("StepInfo", "dump_step_info", "load_step_info")


def _convert_to_paths(paths: Iterable[str]) -> list[Path]:
    return sorted({coerce_path(p) for p in paths})


def _convert_to_strs(words: Iterable[str]) -> list[str]:
    return sorted({str(w) for w in words})


def _current_dir() -> Path:
    return anchor_path(".")


def _normalize_path(path: StrPath) -> Path:
    return coerce_path(path).normpath()


def _check_absolute(_instance, attribute: attrs.Attribute, value: Path):
    if not value.isabs():
        raise ValueError(f"{attribute.name} must be an absolute path, got: {value}")


@attrs.define
class StepInfo:
    """Paths and environment variables of a step, or of another set of dependencies.

    `command` is `None` when the dependencies do not belong to a single step definition,
    e.g. when they were amended by a part of a step.

    All paths and environment variable names are stored in sorted order without duplicates,
    to ensure consistency.
    Relative paths in `inp`, `out` and `vol` are relative to `workdir`.
    Absolute paths are kept as they are.

    The working directory is stored as an absolute path, `abs_workdir`,
    with a flag `is_abs` that records whether it was given as an absolute path.
    The property `workdir` derives the working directory from both fields:
    `abs_workdir` itself when `is_abs` is true,
    and otherwise `abs_workdir` relative to the current directory at the time it is read.
    Hence, `info.workdir / path` is correct wherever and whenever it is evaluated,
    also after the process changed its working directory,
    and step infos created in different directories combine correctly.
    Like any relative path, a copy of `workdir` stored in a variable becomes stale
    after a change of directory, but the `StepInfo` itself does not.
    A step info can be passed as it is to another process with another current directory,
    by pickle or as the JSON of `json_converter` in `stepup.core.cattrs`,
    as long as both processes see the same paths.

    Two step infos are equal when their fields are equal,
    i.e. when they describe the same dependencies in the same directory,
    wherever they were created.

    On Windows, a relative path from one drive to another does not exist.
    After a change to another drive than that of `abs_workdir`,
    `workdir` returns `abs_workdir`, although `is_abs` is false.

    A step info is created with `abs_workdir`,
    or with `StepInfo()` in the current directory followed by `rebase()`.
    For the convention in files, see [`dump_step_info()`][stepup.core.stepinfo.dump_step_info]
    and [`load_step_info()`][stepup.core.stepinfo.load_step_info].
    """

    command: str | None = attrs.field(default=None, converter=attrs.converters.optional(str))
    """The command executed by the step, or `None`."""

    inp: list[Path] = attrs.field(factory=list, converter=_convert_to_paths)
    """List of input paths."""

    env: list[str] = attrs.field(factory=list, converter=_convert_to_strs)
    """List of names of environment variables to which the step is sensitive."""

    out: list[Path] = attrs.field(factory=list, converter=_convert_to_paths)
    """List of output paths."""

    vol: list[Path] = attrs.field(factory=list, converter=_convert_to_paths)
    """List of volatile output paths."""

    abs_workdir: Path = attrs.field(
        factory=_current_dir, converter=_normalize_path, validator=_check_absolute
    )
    """The working directory as an absolute path.

    When `is_abs` is false, it is anchored to a directory whose symbolic links are resolved,
    as with [`anchor_path()`][stepup.core.path.anchor_path].
    The default is the current directory.
    Pass `workdir` instead of this field to API functions,
    because StepUp keeps absolute paths as they are,
    so they would name other files and steps than the relative spelling.
    """

    is_abs: bool = attrs.field(default=False, converter=bool)
    """Whether the working directory was given as an absolute path."""

    @property
    def workdir(self) -> Path:
        """The working directory, relative to the current directory unless `is_abs` is true."""
        return self.abs_workdir if self.is_abs else self.abs_workdir.relpath(anchor_path("."))

    def rebase(self, workdir: StrPath) -> "StepInfo":
        """Express the same dependencies relative to another working directory.

        Parameters
        ----------
        workdir
            The new working directory, relative to the current directory unless absolute.

        Returns
        -------
        step_info
            A copy with normalized paths in `inp`, `out` and `vol`.
            Relative paths are relative to the new `workdir`, absolute paths are kept.
            `command` and `env` are kept.

        Notes
        -----
        When neither working directory is absolute,
        relative paths are moved from one directory to the other without consulting the disk,
        and the result does not depend on `STEPUP_ROOT`.
        When either working directory is absolute,
        relative paths are moved through `STEPUP_ROOT`,
        as with [`translate()`][stepup.core.path.translate]
        and [`translate_back()`][stepup.core.path.translate_back],
        so that an absolute working directory spelled through a symbolic link in `STEPUP_ROOT`
        gives the same paths as its relative spelling.
        If this environment variable is not set, the current directory is taken as the root,
        like in [`get_stepup_root()`][stepup.core.path.get_stepup_root].
        """
        workdir = coerce_path(workdir).normpath()
        return self._rebase(anchor_path(workdir), workdir.isabs())

    def _rebase(self, abs_workdir: Path, is_abs: bool) -> "StepInfo":
        """Express the same dependencies relative to another anchored working directory."""
        if (abs_workdir, is_abs) == (self.abs_workdir, self.is_abs):

            def move(path: Path) -> Path:
                return path.normpath()

        elif not (is_abs or self.is_abs):
            # Both directories are anchored consistently, so a lexical move is exact.
            def move(path: Path) -> Path:
                path = path.normpath()
                if path.isabs():
                    return path
                return (self.abs_workdir / path).normpath().relpath(abs_workdir)

        else:
            # An absolute working directory may be spelled through a symbolic link
            # in `STEPUP_ROOT`, which only `translate()` recognizes.
            old = self.workdir
            new = abs_workdir if is_abs else abs_workdir.relpath(anchor_path("."))

            def move(path: Path) -> Path:
                return translate_back(translate(path, old), new)

        return attrs.evolve(
            self,
            inp=[move(p) for p in self.inp],
            out=[move(p) for p in self.out],
            vol=[move(p) for p in self.vol],
            abs_workdir=abs_workdir,
            is_abs=is_abs,
        )

    def merge(self, *others: "StepInfo") -> "StepInfo":
        """Combine the dependencies of several step infos into one.

        Parameters
        ----------
        others
            The step infos whose dependencies are added to those of `self`.
            Their `workdir` may differ from that of `self`.

        Returns
        -------
        step_info
            A new `StepInfo` with the `command` and working directory of `self`,
            and the union of the dependencies of `self` and `others`.
            The paths of `others` are rebased onto the working directory of `self`,
            as with `rebase()`,
            and all paths are normalized,
            so that different spellings of the same path appear only once.

        Notes
        -----
        A `StepInfo` with a `command` may hold amended dependencies too,
        e.g. after merging the result of
        [`get_amended_info()`][stepup.core.api.get_amended_info] into it.
        To combine step infos in a chosen working directory, merge them into an empty one,
        e.g. `StepInfo().merge(*infos)` for paths relative to the current directory.
        """
        # Rebasing `self` onto its own working directory only normalizes its paths.
        infos = [info._rebase(self.abs_workdir, self.is_abs) for info in (self, *others)]
        return StepInfo(
            self.command,
            [path for info in infos for path in info.inp],
            [name for info in infos for name in info.env],
            [path for info in infos for path in info.out],
            [path for info in infos for path in info.vol],
            self.abs_workdir,
            self.is_abs,
        )

    def filter_inp(self, pattern: str, **subs: str) -> NamedGlob:
        """Return a `NamedGlob` object with matching results from `self.inp`."""
        ng = NamedGlob(pattern, subs)
        ng.extend(self.inp)
        return ng

    def filter_out(self, pattern: str, **subs: str) -> NamedGlob:
        """Return a `NamedGlob` object with matching results from `self.out`."""
        ng = NamedGlob(pattern, subs)
        ng.extend(self.out)
        return ng

    def filter_vol(self, pattern: str, **subs: str) -> NamedGlob:
        """Return a `NamedGlob` object with matching results from `self.vol`."""
        ng = NamedGlob(pattern, subs)
        ng.extend(self.vol)
        return ng


_unstructure_step_info_fields = make_dict_unstructure_fn(StepInfo, json_converter)


def _unstructure_step_info(step_info: StepInfo) -> dict:
    """Unstructure a `StepInfo`, omitting `command` when it is `None`."""
    data = _unstructure_step_info_fields(step_info)
    if step_info.command is None:
        del data["command"]
    return data


json_converter.register_unstructure_hook(StepInfo, _unstructure_step_info)


def load_step_info(filename: StrPath) -> StepInfo | list[StepInfo]:
    """Load one or more step info objects from a JSON file.

    The file should contain a single JSON object or a JSON array of such objects.
    The `command` key may be omitted, in which case `command` is `None`.

    In the file, a relative `workdir` is relative to the directory that contains the file,
    and a missing `workdir` means that directory.
    The paths in `inp`, `out` and `vol` are relative to `workdir`.
    The loaded objects do not depend on the spelling of `filename`,
    and their `workdir` is relative to the current directory unless absolute.
    An absolute `workdir` is kept, with `is_abs` set to true.

    Raises
    ------
    StepUpError
        When an object in the file contains the key `abs_workdir` or `is_abs`,
        i.e. when it was written by `json_converter` instead of `dump_step_info()`.
    """
    with open(filename, encoding="utf-8") as fh:
        data = json.load(fh)
    base = parent_dir(filename)
    if isinstance(data, dict):
        return _from_file(data, filename, base)
    return [_from_file(item, filename, base) for item in data]


def dump_step_info(filename: StrPath, step_info: StepInfo | Iterable[StepInfo]):
    """Dump one or more step info objects to a JSON file.

    The file will contain a single JSON object or a JSON array of such objects.
    The `command` key is omitted when `command` is `None`.

    A relative `workdir` is written relative to the directory that contains the file,
    so that [`load_step_info()`][stepup.core.stepinfo.load_step_info] can read the file
    from any current directory, also after the directory tree was moved.
    An absolute `workdir`, i.e. with `is_abs` set to true, is written as it is.
    The paths in `inp`, `out` and `vol` are written as they are, relative to `workdir`.
    """
    base = parent_dir(filename)
    if isinstance(step_info, StepInfo):
        data = _to_file(step_info, base)
    else:
        data = [_to_file(item, base) for item in step_info]
    with open(filename, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")


def _from_file(data: dict, filename: StrPath, base: str) -> StepInfo:
    """Structure a step info read from a file in the directory `base`."""
    if "abs_workdir" in data or "is_abs" in data:
        raise StepUpError(
            f"A step info in {filename} contains abs_workdir or is_abs instead of workdir."
        )
    workdir = coerce_path(data.pop("workdir", "."))
    data["abs_workdir"] = anchor_path(workdir, base)
    data["is_abs"] = workdir.isabs()
    return json_converter.structure(data, StepInfo)


def _to_file(step_info: StepInfo, base: str) -> dict:
    """Unstructure a step info to be written to a file in the directory `base`."""
    data = json_converter.unstructure(step_info)
    del data["abs_workdir"], data["is_abs"]
    data["workdir"] = str(
        step_info.abs_workdir
        if step_info.is_abs
        else step_info.abs_workdir.relpath(anchor_path(".", base))
    )
    return data
