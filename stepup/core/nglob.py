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
"""Glob with named back-reference support.

A named glob (nglob) pattern may contain the following:

- ``${*name}`` is a named wildcard. Optionally, the name can be associated with a glob pattern.
  When no pattern is specified for a name, the default is ``*``.
  When a name appears twice in a named glob pattern (or multiple named glob patterns),
  the same value must be present for each placeholder to get a match.
- One may also specify a set of named glob patterns, enforcing consistency between their names.
- Regular wildcards are also allowed and are called "anonymous wildcards"
  to clarify the distinction with named wildcards.
"""

import copy
import glob
import re
from typing import Iterator, Iterable, Self, Collection, Any

import attrs
from path import Path


RE_NAMED_WILD = re.compile(r"(\[.*?]|\$\{\*[a-zA-Z0-9_]*?}|[*]{1,2}|[?])")


__all__ = (
    "NGlobMatch",
    "NGlobSingle",
    "NGlobMulti",
    "convert_nglob_to_regex",
    "convert_nglob_to_glob",
    "has_wildcards",
)


@attrs.define
class NGlobMatch:
    _mapping: dict[str, str]
    _files: list[Path | list[Path]]

    def __getitem__(self, idx) -> Path | list[Path]:
        return self._files[idx]

    def __getattr__(self, name) -> str:
        try:
            return self._mapping[name]
        except KeyError:
            raise AttributeError(f"'NGlobMatch' object has not attribute '{name}'")

    @property
    def mapping(self) -> dict[str, str]:
        return self._mapping

    @property
    def files(self) -> list[Path]:
        return self._files


@attrs.define
class NGlobSingle:
    """Named glob with a single pattern."""

    _pattern: str = attrs.field()
    _subs: dict[str, str] = attrs.field(factory=dict)
    _results: dict[tuple[str, ...], set[Path]] = attrs.field(factory=dict)
    _used_names: tuple[str, ...] = attrs.field(init=False)
    _glob_pattern: str = attrs.field(init=False)
    _regex: re.Pattern = attrs.field(init=False)

    @classmethod
    def structure(cls, state, strings: list[str]):
        results = {
            tuple(values): {Path(strings[path]) for path in paths} for values, paths in state["r"]
        }
        return cls(strings[state["p"]], state["s"], results)

    def unstructure(self, lookup: dict[str, int]) -> dict[str, Any]:
        results = [
            (key, [lookup[path] for path in sorted(paths)]) for key, paths in self._results.items()
        ]
        return {"p": lookup[self.pattern], "s": self.subs, "r": results}

    @_used_names.default
    def _default_used_names(self) -> tuple[str, ...]:
        return tuple(sorted(set(iter_wildcard_names(self._pattern))))

    @_glob_pattern.default
    def _default_glob(self) -> str:
        return convert_nglob_to_glob(self._pattern, self._subs)

    @_regex.default
    def _default_regex(self) -> re.Pattern:
        return re.compile(convert_nglob_to_regex(self._pattern, self._subs))

    @property
    def pattern(self) -> str:
        return self._pattern

    @property
    def subs(self) -> dict[str, str]:
        return self._subs

    @property
    def results(self) -> dict[tuple[str, ...], set[Path]]:
        return self._results

    @property
    def used_names(self) -> tuple[str, ...]:
        return self._used_names

    @property
    def glob_pattern(self) -> str:
        return self._glob_pattern

    @property
    def regex(self) -> re.Pattern:
        return self._regex

    def _loop_matches(
        self, paths: Iterable[str]
    ) -> Iterator[tuple[tuple[str, ...], set[Path], Path]]:
        for path in paths:
            match_ = self._regex.fullmatch(path)
            if match_ is not None:
                mapping = match_.groupdict()
                values = tuple(mapping[name] for name in self._used_names)
                paths = self._results.get(values)
                if paths is None:
                    paths = set()
                    self._results[values] = paths
                yield values, paths, Path(path)
                if len(paths) == 0:
                    del self._results[values]

    def extend(self, paths: Iterable[str]) -> Iterator[tuple[str, ...]]:
        """Add matching paths from the given list paths. Yield new values for named wildcards."""
        for values, path_set, path in self._loop_matches(paths):
            if len(path_set) == 0:
                yield values
            path_set.add(path)

    def reduce(self, paths: Iterable[str]) -> Iterator[tuple[str, ...]]:
        """Remove matching paths from given list paths. Yield deleted values of named wildcards."""
        for values, path_set, path in self._loop_matches(paths):
            if len(path_set) > 0:
                path_set.discard(path)
                if len(path_set) == 0:
                    yield values

    def glob(self) -> Iterator[tuple[str, ...]]:
        yield from self.extend(glob.glob(self._glob_pattern, recursive=True))


def has_wildcards(pattern: str) -> bool:
    """Test if a glob pattern has anonymous or named wildcards."""
    return RE_NAMED_WILD.search(pattern) is not None


def has_anonymous_wildcards(pattern: str) -> bool:
    """Test if a glob pattern has anonymous wildcards."""
    for ipart, part in enumerate(RE_NAMED_WILD.split(pattern)):
        if ipart % 2 == 1 and not part.startswith("${*"):
            return True
    return False


def iter_wildcard_names(pattern: str) -> Iterator[str]:
    for ipart, part in enumerate(RE_NAMED_WILD.split(pattern)):
        if ipart % 2 == 1 and part.startswith("${*"):
            yield part[3:-1]


@attrs.define
class NGlobMulti:
    _nglob_singles: tuple[NGlobSingle, ...] = attrs.field()
    _subs: dict[str, str] = attrs.field(init=False)
    _used_names: tuple[str, ...] = attrs.field(init=False)
    _has_wildcards: bool = attrs.field(init=False)
    _results: dict[tuple[str, ...], list[set[Path]]] = attrs.field(init=False, factory=dict)

    @_subs.default
    def _default_subs(self):
        if len(self._nglob_singles) == 0:
            return {}
        else:
            subs = self._nglob_singles[0].subs
            for other in self._nglob_singles[1:]:
                if other.subs != subs:
                    raise ValueError("Searches in one NGlobMulti must use the same substitutions")
                other._subs = subs
            return subs

    @_used_names.default
    def _default_used_names(self) -> tuple[str, ...]:
        result = set()
        for ngs in self._nglob_singles:
            result.update(ngs.used_names)
        return tuple(sorted(result))

    @_has_wildcards.default
    def _default_has_wildcards(self) -> bool:
        for ngs in self._nglob_singles:
            if has_anonymous_wildcards(ngs.pattern):
                return True
        for name in self._used_names:
            pattern = self._subs.get(name)
            if pattern is None:
                return True
            if has_anonymous_wildcards(pattern):
                return True
        return False

    @classmethod
    def structure(cls, state: list, strings: list[str]) -> Self:
        subs = state[1].copy()
        for ngs_data in state[0]:
            ngs_data["s"] = subs
        ngss = tuple(NGlobSingle.structure(ngs_data, strings) for ngs_data in state[0])
        result = cls(ngss)
        for i, ngs in enumerate(ngss):
            for values in ngs.results:
                result._extend_consistent(i, values)
        return result

    def unstructure(self, lookup: dict[str, int]) -> list:
        data = [
            [ngs.unstructure(lookup) for ngs in self._nglob_singles],
            self._subs.copy(),
        ]
        # A little hacky way to make the result more compact.
        for ngs_data in data[0]:
            del ngs_data["s"]
        return data

    @classmethod
    def from_patterns(cls, patterns: Iterable[str], subs: dict[str, str] = None) -> Self:
        if isinstance(patterns, str):
            raise TypeError("The patterns argument cannot be a string")
        if not all(isinstance(pattern, str) for pattern in patterns):
            raise TypeError(f"The patterns must be a list of strings, got {patterns}")
        if subs is None:
            subs = {}
        else:
            if not all(isinstance(name, str) for name in subs):
                raise TypeError(f"The subs keys must be a list of strings, got {patterns}")
            if not all(isinstance(value, str) for value in subs.values()):
                raise TypeError(f"The subs values must be a list of strings, got {patterns}")
        return cls(tuple(NGlobSingle(str(pattern), subs) for pattern in patterns))

    @property
    def nglob_singles(self) -> tuple[NGlobSingle, ...]:
        return self._nglob_singles

    @property
    def patterns(self):
        return [ngs.pattern for ngs in self._nglob_singles]

    @property
    def subs(self) -> dict[str, str]:
        return self._subs

    @property
    def used_names(self) -> tuple[str, ...]:
        return self._used_names

    @property
    def has_wildcards(self) -> bool:
        return self._has_wildcards

    @property
    def results(self) -> dict[tuple[str, ...], list[set[Path]]]:
        return self._results

    def _iter_consistent(self, criteria: dict[str, str], full_paths: list | int):
        start = full_paths if isinstance(full_paths, int) else len(full_paths)
        if start == len(self._nglob_singles):
            yield tuple(criteria[name] for name in self._used_names), full_paths
        else:
            ngs = self._nglob_singles[start]
            for new_values, paths in ngs.results.items():
                next_criteria = criteria.copy()
                for name, new_value in zip(ngs.used_names, new_values):
                    value = next_criteria.get(name)
                    if value is None:
                        next_criteria[name] = new_value
                    elif value != new_value:
                        next_criteria = None
                        break
                if next_criteria is not None:
                    next_full_paths = (
                        start + 1 if isinstance(full_paths, int) else [*full_paths, paths]
                    )
                    yield from self._iter_consistent(next_criteria, next_full_paths)

    def _extend_consistent(self, i: int, values: tuple[str, ...]):
        criteria = dict(zip(self._nglob_singles[i].used_names, values))
        new_items = list(self._iter_consistent(criteria, []))
        for full_values, full_paths in new_items:
            self._results[full_values] = full_paths

    def _reduce_consistent(self, i: int, values: tuple[str, ...]):
        criteria = dict(zip(self._nglob_singles[i].used_names, values))
        old_items = list(self._iter_consistent(criteria, 0))
        for full_values, _ in old_items:
            del self._results[full_values]

    def extend(self, paths: Iterable[str]):
        """Find matches from a list of paths. Return new full matches (and track partial matches)"""
        if isinstance(paths, str):
            raise TypeError("The paths argument cannot be a string.")
        for i, ngs in enumerate(self._nglob_singles):
            for values in ngs.extend(paths):
                self._extend_consistent(i, values)

    def reduce(self, paths: Iterable[str]):
        """Drop matches based on deleted paths. Return old full matches (and track partial)."""
        if isinstance(paths, str):
            raise TypeError("The paths argument cannot be a string.")
        for i, ngs in enumerate(self._nglob_singles):
            for values in ngs.reduce(paths):
                self._reduce_consistent(i, values)

    def glob(self):
        for i, ngs in enumerate(self._nglob_singles):
            for values in ngs.glob():
                self._extend_consistent(i, values)

    def deepcopy(self):
        """Return an independent copy."""
        return copy.deepcopy(self)

    def equals(self, other: "NGlobMulti") -> bool:
        """Compare the results."""
        return self._results == other._results

    # Convenience methods

    def matches(self) -> Iterator[NGlobMatch]:
        """Iterate over combinations of files that consistently match of all patterns."""
        for values, path_sets in sorted(self._results.items()):
            mapping = dict(zip(self._used_names, values))
            files = [
                (sorted(paths) if has_anonymous_wildcards(ngs.pattern) else next(iter(paths)))
                for ngs, paths in zip(self._nglob_singles, path_sets)
            ]
            yield NGlobMatch(mapping, files)

    def files(self) -> tuple[Path, ...]:
        """Return a tuple of sorted files that match the individual patterns.

        No constraints between multiple patterns are imposed and files may belong to partial
        and inconsistent full matches.
        """
        result = set()
        for ngs in self._nglob_singles:
            for path_set in ngs.results.values():
                result.update(path_set)
        return tuple(sorted(result))

    def __bool__(self):
        """True when there were some matches."""
        return len(self.results) > 0

    def __iter__(self) -> Iterator[str | NGlobMatch]:
        if len(self._used_names) > 0:
            return self.matches()
        else:
            return iter(self.files())

    def may_match(self, path):
        """Return True if the path matches one of the NGlobSingle instances.

        This means that it may be path contributing to a consistent match of NGlobMulti.
        When added, it will show up in the result of the `files` method and it may affect the
        outcome of the `matches` method.
        """
        for ngs in self._nglob_singles:
            if ngs.regex.fullmatch(path):
                return True
        return False

    def may_change(self, deleted: set[str], added: set[str]) -> bool:
        """Determine whether the results may change (later) after deleting or adding files.

        Parameters
        ----------
        deleted
            Set of files to be deleted.
        added
            Set of files to be added.

        Returns
        -------
        may_change
            True if the NGlobMulti results may change.
            (It may require additional additions and deletions to get any effect,
            but cannot be excluded that that is possible.)
        """
        added_new = added.copy()
        for ngs in self._nglob_singles:
            for paths in ngs.results.values():
                if not deleted.isdisjoint(paths):
                    return True
                added_new.difference_update(paths)
        for ngs in self._nglob_singles:
            for path in added_new:
                if ngs.regex.fullmatch(path):
                    return True
        return False

    def will_change(self, deleted: Collection[str], updated: Collection[str]) -> bool:
        """Determine whether the results may change after deleting or adding files.

        Parameters
        ----------
        deleted
            Set of files to be deleted.
        updated
            Set of files to be added or changed.

        Returns
        -------
        will_change
            True if the NGlobMulti results will change.
        """
        evolved = self.deepcopy()
        evolved.extend(updated)
        evolved.reduce(deleted)
        return not evolved.equals(self)


def convert_nglob_to_regex(
    pattern: str, subs: dict[str, str] | None = None, allow_names: bool = True
) -> str:
    """Convert a named glob pattern to a regular expressions.

    Parameters
    ----------
    pattern
        A string with named wildcards.
    subs
        A dictionary mapping names to glob patterns.
        If a name is not present, "*" is used as default.
    allow_names
        When set to False, named wildcards are not allowed.

    Returns
    -------
    regex
        A regex string to test if a string matches the pattern.
        It also contains symbolic groups to extract values
        corresponding to named wildcards.
    """
    if subs is None:
        subs = {}
    parts = []
    encountered = set()
    for i, part in enumerate(RE_NAMED_WILD.split(pattern)):
        if i % 2 == 0:
            # Not a wildcard: escape regex characters.
            parts.append(re.escape(part))
        else:
            # A (named) wildcard: replace with corresponding regex.
            if part == "?":
                regex = r"[^/]"
            elif part == "*":
                regex = r"[^/]*"
            elif part == "**":
                regex = r".*"
            elif part.startswith("[") and part.endswith("]"):
                if part[1] == "!":
                    regex = rf"[^{part[2:-1]}]"
                else:
                    regex = rf"[{part[1:-1]}]"
            elif part.startswith("${*") and part.endswith("}"):
                if not allow_names:
                    raise ValueError(f"Named wildcards not allowed in {pattern}")
                name = part[3:-1]
                if name in encountered:
                    regex = rf"(?P={name})"
                else:
                    part_regex = convert_nglob_to_regex(subs.get(name, "*"), {}, False)
                    regex = rf"(?P<{name}>{part_regex})"
                    encountered.add(name)
            else:
                raise ValueError(f"Cannot convert wildcard to regex: {part}")
            parts.append(regex)
    return "".join(parts)


def convert_nglob_to_glob(pattern: str, subs: dict[str, str] | None = None) -> str:
    """Convert nglob wildcards to ordinary ones, compatible with builtin glob and fnmatch.

    Parameters
    ----------
    pattern
        A string with named wildcards.
    subs
        A dictionary mapping names to glob patterns.
        If a name is not present, "*" is used as default.

    Returns
    -------
        A conventional wildcard string, without the constraint that named wildcards must correspond.
        Where possible, neighbouring wildcards are merged into one.
    """
    if subs is None:
        subs = {}
    # Split in text, wildcard and named wildcard fragments.
    parts = []
    # The odd-numbered indices match a (named) wildcard.
    for i, part in enumerate(RE_NAMED_WILD.split(pattern)):
        if i % 2 == 1 and part.startswith("${*"):
            # Split the substituted named wildcards once more.
            parts.extend(RE_NAMED_WILD.split(subs.get(part[3:-1], "*")))
        else:
            # No substitution, so no additional splitting required.
            parts.append(part)
    # Remove empty strings due to neighboring wildcards with no normal text in between.
    parts = [part for part in parts if part != ""]
    # Make sure no asterisks are glued together and a few other simplifications.
    texts = []
    for part in parts:
        if len(texts) == 0:
            texts.append(part)
        elif part == "?":
            if texts[-1] not in ["*", "**"]:
                texts.append(part)
        elif part == "*":
            if texts[-1] == "?":
                texts[-1] = "*"
            elif texts[-1] not in ["*", "**"]:
                texts.append("*")
        elif part == "**":
            if texts[-1] in ["*", "?"]:
                texts[-1] = "**"
            elif texts[-1] != "**":
                texts.append("**")
        else:
            texts.append(part)
    return "".join(texts)
