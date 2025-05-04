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
"""Information about a step in a StepUp build, intended for defining follow-up steps."""

import json
from collections.abc import Iterable

import attrs
from path import Path

from .nglob import NGlobMulti

__all__ = ("StepInfo", "dump_step_info", "load_step_info")


def _convert_to_paths(paths: Iterable[str]) -> list[Path]:
    return sorted(Path(p) for p in paths)


def _convert_to_strs(words: Iterable[str]) -> list[str]:
    return sorted(str(w) for w in words)


@attrs.define
class StepInfo:
    """The `step()` function returns an instance of this class to help defining follow-up steps.

    This object will not contain any information that is amended while the step is executed.
    It only holds information known at the time the step is defined.

    All paths and environment variables are stored in sorted order to ensure consistency.
    """

    action: str = attrs.field(converter=str)
    """The action of the step."""

    workdir: Path = attrs.field(converter=Path)
    """The work directory of the step.

    If relative, it is relative to the StepUp root."""

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


def load_step_info(filename: str) -> StepInfo | list[StepInfo]:
    """Load one or more step info object from a JSON file.

    The file should contain a single JSON object or a JSON array of such objects.
    """
    with open(filename) as fh:
        data = json.load(fh)
        return StepInfo(**data) if isinstance(data, dict) else [StepInfo(**item) for item in data]


def dump_step_info(filename: str, step_info: StepInfo | list[StepInfo]):
    """Dump one or more step info objects to a JSON file.

    The file will contain a single JSON object or a JSON array of such objects.
    """
    with open(filename, "w") as fh:
        data = (
            attrs.asdict(step_info)
            if isinstance(step_info, StepInfo)
            else [attrs.asdict(si) for si in step_info]
        )
        json.dump(data, fh, indent=2)
        fh.write("\n")
