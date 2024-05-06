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
"""Bidirectional non-exclusive map."""

import enum
from collections.abc import Iterator
from typing import Generic, TypeVar

import attrs

__all__ = ("Assoc", "AssocView", "one_to_one", "one_to_many", "many_to_one", "many_to_many")


# Taken from attrs._make
class _Nothing(enum.Enum):
    NOTHING = enum.auto()

    def __repr__(self):
        return "NOTHING"

    def __bool__(self):
        return False


NOTHING = _Nothing.NOTHING


MAX_VALIDATOR = attrs.validators.optional(
    attrs.validators.and_(attrs.validators.instance_of(int), attrs.validators.ge(1))
)


S = TypeVar("S")
D = TypeVar("D")


@attrs.define
class AssocView(Generic[S, D]):
    _dict: dict[S, set[D]] = attrs.field(init=False, factory=dict)
    # The maximum number of destinations
    _max: int | None = attrs.field(default=None, validator=MAX_VALIDATOR)
    _inverse: "AssocView[D, S]" = attrs.field(init=False, default=None)

    @property
    def max(self) -> int:
        return self._max

    @property
    def inverse(self) -> "AssocView[D, S]":
        return self._inverse

    def __len__(self) -> int:
        return len(self._dict)

    def __contains__(self, src: S) -> bool:
        return src in self._dict

    def __getitem__(self, src: S) -> D | set[D]:
        dsts = self._dict[src]
        if self.max == 1:
            assert len(dsts) == 1
            return next(iter(dsts))
        return frozenset(dsts)

    def __setitem__(self, src: S, dsts: D | set[D]):
        # Interpret dsts
        if self._max == 1:
            dsts = (dsts,)
        # Get relevant info
        inverse_dict = self._inverse._dict
        inverse_max = self._inverse._max
        # Perform checks
        if self._max is not None and len(dsts) > self._max:
            raise ValueError(f"len(dsts) = {len(dsts)} > {self._max}")
        for dst in dsts:
            src_set = inverse_dict.get(dst)
            if (
                src_set is not None
                and inverse_max is not None
                and len(src_set) >= inverse_max
                and src not in src_set
            ):
                raise ValueError(f"len(src_set) = {len(src_set) + 1} > {inverse_max}")
        # Apply changes
        if self._dict.get(src) is not None:
            self.discard(src)
        self._dict[src] = set(dsts)
        for dst in dsts:
            src_set = inverse_dict.get(dst)
            if src_set is None:
                src_set = set()
                inverse_dict[dst] = src_set
            src_set.add(src)

    def get(self, src: S, default=None):
        dsts = self._dict.get(src)
        if dsts is None:
            return default
        if self.max == 1:
            assert len(dsts) == 1
            return next(iter(dsts))
        return frozenset(dsts)

    def has(self, src: S, dst: D) -> bool:
        dsts = self._dict.get(src)
        return dsts is not None and dst in dsts

    def keys(self) -> Iterator[S]:
        yield from self._dict.keys()

    def items(self) -> Iterator[tuple[S, D | set[D]]]:
        if self._max == 1:
            for src, dsts in self._dict.items():
                yield src, next(iter(dsts))
        else:
            yield from self._dict.items()

    def pairs(self) -> Iterator[tuple[S, D]]:
        for src, dsts in self._dict.items():
            for dst in sorted(dsts):
                yield src, dst

    def add(self, src: S, dst: D):
        # Get sets
        dst_set = self._dict.get(src)
        if dst_set is None:
            dst_set = set()
        src_set = self._inverse._dict.get(dst)
        if src_set is None:
            src_set = set()
        # Perform checks
        if self._max is not None and len(dst_set) >= self._max and dst not in dst_set:
            raise ValueError(f"Cannot not have more than {self._max} dst key(s) for one src key")
        if (
            self._inverse._max is not None
            and len(src_set) >= self._inverse._max
            and src not in src_set
        ):
            raise ValueError(
                f"Cannot not have more than {self._inverse._max} src key(s) for one dst key"
            )
        # Apply changes
        self._dict[src] = dst_set
        self._inverse._dict[dst] = src_set
        dst_set.add(dst)
        src_set.add(src)

    def discard(self, src: S, dst: D | _Nothing = NOTHING, *, insist=False):
        """Discard one or more relations.

        Parameters
        ----------
        src
            The source.
        dst
            The destination.
        insist
            When True, a KeyError is raised when nothing was found to be removed.
        """
        dst_set = self._dict.get(src)
        if dst_set is None:
            if insist:
                raise KeyError("No edges found to be discarded.")
            return
        if dst is NOTHING:
            for dst in dst_set:
                src_set = self._inverse._dict[dst]
                src_set.discard(src)
                if len(src_set) == 0:
                    del self._inverse._dict[dst]
            del self._dict[src]
        elif dst not in dst_set:
            if insist:
                raise KeyError("No edge found to be discarded.")
        else:
            dst_set.discard(dst)
            if len(dst_set) == 0:
                del self._dict[src]
            src_set = self._inverse._dict.get(dst)
            src_set.discard(src)
            if len(src_set) == 0:
                del self._inverse._dict[dst]

    def clear(self):
        """Remove all contents"""
        self._dict.clear()
        self._inverse._dict.clear()


@attrs.define
class Assoc(AssocView[S, D]):
    # The maximum number of sources
    _inverse_max: int | None = attrs.field(default=None, validator=MAX_VALIDATOR)

    def __attrs_post_init__(self):
        self._inverse = AssocView(self._inverse_max)
        self._inverse._inverse = self


#
# Convenient constructors
#


def one_to_one() -> Assoc:
    return Assoc(1, 1)


def one_to_many(dst_max: int | None = None) -> Assoc:
    return Assoc(dst_max, 1)


def many_to_one(src_max: int | None = None) -> Assoc:
    return Assoc(1, src_max)


def many_to_many(src_max: int | None = None, dst_max: int | None = None) -> Assoc:
    return Assoc(dst_max, src_max)
