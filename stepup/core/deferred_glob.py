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
"""Deferred patterns for static files."""

from collections.abc import Iterator
from typing import TYPE_CHECKING, Any, Self

import attrs

from .cascade import Node
from .nglob import NGlobMulti
from .utils import classproperty

if TYPE_CHECKING:
    from .workflow import Workflow


__all__ = ("DeferredGlob",)


@attrs.define
class DeferredGlob(Node):
    """A pattern to make files static when they are used as input."""

    _ngm: NGlobMulti = attrs.field()

    #
    # Getters
    #

    @classproperty
    def kind(cls):
        return "dg"

    @property
    def ngm(self) -> NGlobMulti:
        return self._ngm

    #
    # Initialization, serialization and formatting
    #

    @staticmethod
    def valid_ngm(ngm: NGlobMulti):
        return len(ngm.used_names) == 0 and len(ngm.subs) == 0

    @classmethod
    def key_tail(cls, data: dict[str, Any], strings: list[str] | None = None) -> str:
        """Subclasses must implement the key tail and accept both JSON or attrs dicts."""
        ngm = data.get("_ngm")
        if ngm is None:
            ngm_data = data.get("g")
            assert ngm_data is not None
            ngm = NGlobMulti.structure(ngm_data, strings)
        if not cls.valid_ngm(ngm):
            raise ValueError("Deferred globs cannot contain named wildcards")
        return " ".join(repr(ngs.pattern) for ngs in ngm.nglob_singles)

    @classmethod
    def structure(cls, workflow: "Workflow", strings: list[str], data: dict) -> Self:
        ngm = NGlobMulti.structure(data["g"], strings)
        if not cls.valid_ngm(ngm):
            raise ValueError("Deferred globs cannot contain named wildcards")
        return cls(ngm)

    def unstructure(self, workflow: "Workflow", lookup: dict[str, int]) -> dict:
        return {"g": self.ngm.unstructure(lookup)}

    def format_properties(self, workflow: "Workflow") -> Iterator[tuple[str, str]]:
        yield from []
