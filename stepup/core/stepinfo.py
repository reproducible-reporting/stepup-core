# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Information about a step in a StepUp build, intended for defining follow-up steps."""

import json
from collections.abc import Iterable

import attrs
from cattrs.gen import make_dict_unstructure_fn

from .cattrs import json_converter
from .nglob import NamedGlob
from .path import Path, StrPath, coerce_path, translate, translate_back

__all__ = ("StepInfo", "dump_step_info", "load_step_info")


def _convert_to_paths(paths: Iterable[str]) -> list[Path]:
    return sorted({coerce_path(p) for p in paths})


def _convert_to_strs(words: Iterable[str]) -> list[str]:
    return sorted({str(w) for w in words})


@attrs.define
class StepInfo:
    """Paths and environment variables of a step, or of another set of dependencies.

    `command` is `None` when the dependencies do not belong to a single step definition,
    e.g. when they were amended by a part of a step.

    All paths and environment variable names are stored in sorted order without duplicates,
    to ensure consistency.
    Relative paths in `inp`, `out` and `vol` are relative to `workdir`,
    and a relative `workdir` is relative to `STEPUP_ROOT`.
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

    workdir: Path = attrs.field(default=Path("."), converter=coerce_path)
    """The working directory, relative to `STEPUP_ROOT` unless absolute."""

    def rebase(self, workdir: StrPath) -> "StepInfo":
        """Express the same dependencies relative to another working directory.

        Parameters
        ----------
        workdir
            The new working directory, relative to `STEPUP_ROOT` unless absolute.

        Returns
        -------
        step_info
            A copy with normalized paths in `inp`, `out` and `vol`.
            Relative paths are relative to the new `workdir`, absolute paths are kept.
            `command` and `env` are kept.

        Notes
        -----
        When the working directory changes, relative paths are resolved through `STEPUP_ROOT`.
        If this environment variable is not set, the current directory is taken as the root,
        like in [`get_stepup_root()`][stepup.core.path.get_stepup_root].
        The result does not depend on the current directory otherwise.
        """
        workdir = coerce_path(workdir).normpath()
        if workdir == self.workdir.normpath():
            return attrs.evolve(
                self,
                inp=[p.normpath() for p in self.inp],
                out=[p.normpath() for p in self.out],
                vol=[p.normpath() for p in self.vol],
                workdir=workdir,
            )
        # Both working directories relative to the current directory.
        old = translate_back(self.workdir)
        new = translate_back(workdir)
        return attrs.evolve(
            self,
            inp=[translate_back(translate(p, old), new) for p in self.inp],
            out=[translate_back(translate(p, old), new) for p in self.out],
            vol=[translate_back(translate(p, old), new) for p in self.vol],
            workdir=workdir,
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
            A new `StepInfo` with the `command` and `workdir` of `self`,
            and the union of the dependencies of `self` and `others`.
            The paths of `others` are rebased onto `workdir`, as with `rebase()`,
            and all paths are normalized,
            so that different spellings of the same path appear only once.

        Notes
        -----
        A `StepInfo` with a `command` may hold amended dependencies too,
        e.g. after merging the result of
        [`get_amended_info()`][stepup.core.api.get_amended_info] into it.
        To combine step infos in a chosen working directory, merge them into an empty one,
        e.g. `StepInfo(workdir=".").merge(*infos)` for paths relative to `STEPUP_ROOT`.
        """
        # Rebasing `self` onto its own `workdir` only normalizes its paths.
        infos = [info.rebase(self.workdir) for info in (self, *others)]
        return StepInfo(
            self.command,
            [path for info in infos for path in info.inp],
            [name for info in infos for name in info.env],
            [path for info in infos for path in info.out],
            [path for info in infos for path in info.vol],
            infos[0].workdir,
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
    """
    with open(filename, encoding="utf-8") as fh:
        data = json.load(fh)
    if isinstance(data, dict):
        return json_converter.structure(data, StepInfo)
    return json_converter.structure(data, list[StepInfo])


def dump_step_info(filename: StrPath, step_info: StepInfo | Iterable[StepInfo]):
    """Dump one or more step info objects to a JSON file.

    The file will contain a single JSON object or a JSON array of such objects.
    The `command` key is omitted when `command` is `None`.
    """
    with open(filename, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(json_converter.unstructure(step_info), fh, indent=2)
        fh.write("\n")
