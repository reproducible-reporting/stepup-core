# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Information about a step in a StepUp build, intended for defining follow-up steps."""

import json
from collections.abc import Iterable

import attrs
from path import Path

from .cattrs import json_converter
from .nglob import NGlobMulti
from .path import StrPath, coerce_path

__all__ = ("StepInfo", "dump_step_info", "load_step_info")


def _convert_to_paths(paths: Iterable[str]) -> list[Path]:
    return sorted(coerce_path(p) for p in paths)


def _convert_to_strs(words: Iterable[str]) -> list[str]:
    return sorted(str(w) for w in words)


@attrs.define
class StepInfo:
    """The `step()` function returns an instance of this class to help defining follow-up steps.

    This object will not contain any information that is amended while the step is executed.
    It only holds information known at the time the step is defined.

    All paths and environment variables are stored in sorted order to ensure consistency.
    """

    command: str = attrs.field(converter=str)
    """The command to be executed of the step."""

    inp: list[Path] = attrs.field(converter=_convert_to_paths)
    """List of input paths of the step.

    If relative, they are relative to the work directory.
    """

    env: list[str] = attrs.field(converter=_convert_to_strs)
    """List of environment values used by the step."""

    out: list[Path] = attrs.field(converter=_convert_to_paths)
    """List of output paths of the step.

    If relative, they are relative to the work directory.
    """

    vol: list[Path] = attrs.field(converter=_convert_to_paths)
    """List of volatile output paths of the step.

    If relative, they are relative to the work directory.
    """

    workdir: Path = attrs.field(converter=coerce_path)
    """The work directory of the step.

    If relative, it is relative to the StepUp root."""

    def filter_inp(self, *patterns: str, **subs: str):
        """Return an `NGlobMulti` object with matching results from `self.inp`."""
        ngm = NGlobMulti.from_patterns(patterns, subs)
        ngm.extend(self.inp)
        return ngm

    def filter_out(self, *patterns: str, **subs: str):
        """Return an `NGlobMulti` object with matching results from `self.out`."""
        ngm = NGlobMulti.from_patterns(patterns, subs)
        ngm.extend(self.out)
        return ngm

    def filter_vol(self, *patterns: str, **subs: str):
        """Return an `NGlobMulti` object with matching results from `self.vol`."""
        ngm = NGlobMulti.from_patterns(patterns, subs)
        ngm.extend(self.vol)
        return ngm


def load_step_info(filename: StrPath) -> StepInfo | list[StepInfo]:
    """Load one or more step info object from a JSON file.

    The file should contain a single JSON object or a JSON array of such objects.
    """
    with open(filename) as fh:
        data = json.load(fh)
    if isinstance(data, dict):
        return json_converter.structure(data, StepInfo)
    return json_converter.structure(data, list[StepInfo])


def dump_step_info(filename: StrPath, step_info: StepInfo | Iterable[StepInfo]):
    """Dump one or more step info objects to a JSON file.

    The file will contain a single JSON object or a JSON array of such objects.
    """
    with open(filename, "w") as fh:
        json.dump(json_converter.unstructure(step_info), fh, indent=2)
        fh.write("\n")
