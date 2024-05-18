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

Named glob (NGlob) patterns are an advanced form of pattern matching
that supports back referencing of previously matched substrings.

It has the following use cases:

- **Single named wildcard:**
    By default, the wildcard `${*name}` is a placeholder for any string.
    One may also specify a pattern for `${*name}` through optional arguments.
    For example:

    ```python
    ngs = NGlobSingle("feedback_${*idx}.md", idx="[0-9][0-9][0-9]")
    ngs.glob()
    print(ngs.results)
    ```

- **Consistency within one pattern:**
    If a pattern uses the same named globs multiple times,
    the matching substring must also be consistent.
    For example:

    ```python
    ngs = NGlobSingle("archive_${*idx}/feedback_${*idx}.md", idx="[0-9][0-9][0-9]")
    ngs.glob()
    print(ngs.results)
    ```

    These would match:

    - `archive_042/feedback_042.md`
    - `archive_777/feedback_777.md`

    This won't match:

    - `archive_042/feedback_777.md`

- **Consistency across multiple patterns:**
    One can define multiple patterns and enforce consistency between their matches.
    For example:

    ```python
    ngm = NGlobMulti("feedback_${*idx}.md", "report_${*idx}.pdf", idx="[0-9][0-9][0-9]")
    ngm.glob()
    print(ngm.results)
    ```

    This will produce pairs of matches (provided the files are present).
    For example, the following would match:

    - `feedback_001.md` with `report_001.pdf`
    - `feedback_123.md` with `report_123.pdf`

    The following won't be in the results, despite the fact that the files exist:

    - `feedback_001.md` with `report_123.pdf`

- Conventional (recursive) glob wildcards are also allowed and are called "anonymous wildcards"
  to clarify the distinction from named wildcards.
"""

import copy
import glob
import re
from collections.abc import Collection, Iterable, Iterator
from typing import Any, Self

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
    """A set of matches corresponding sharing consistent values for named wildcards.

    The matching files can be accessed by integer indexing or through the `files` attribute:

    ```python
    assert match[0] == match.files[0]
    ```

    The substring matching the named wildcards can be accessed as attributes.
    For example, the substring matching a named wildcard `foo` is accessed as follows:

    ```python
    print(match.foo)
    ```
    """

    _mapping: dict[str, str]
    _files: list[Path | list[Path]]

    def __getitem__(self, idx) -> Path | list[Path]:
        return self._files[idx]

    def __getattr__(self, name) -> str:
        try:
            return self._mapping[name]
        except KeyError as exc:
            raise AttributeError(f"'NGlobMatch' object has no attribute '{name}'") from exc

    @property
    def mapping(self) -> dict[str, str]:
        """Dictionary with `(wildcard_name, substring)` items."""
        return self._mapping

    @property
    def files(self) -> list[Path | list[Path]]:
        """Matching files, all having consistent substrings matching the named wildcards.

        Each item corresponds to a pattern in `NGlobMulti.patterns`.
        If a pattern has anonymous wildcards,
        the item itself is a list of all files matching the pattern,
        If the pattern contains no anonymous wildcards,
        the corresponding item in the returned list is a single path.
        """
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
        """Create an instance from the result of the `unstructure` method."""
        results = {
            tuple(values): {Path(strings[path]) for path in paths} for values, paths in state["r"]
        }
        return cls(strings[state["p"]], state["s"], results)

    def unstructure(self, lookup: dict[str, int]) -> dict[str, Any]:
        """Return a serializable representation of the instance."""
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
        """The Named Glob pattern used to match filenames."""
        return self._pattern

    @property
    def subs(self) -> dict[str, str]:
        """User-defined glob patterns for the named wildcards.

        When a name is not present, `*` is used.
        """
        return self._subs

    @property
    def results(self) -> dict[tuple[str, ...], set[Path]]:
        """All matching files, grouped by substrings matching the named wildcards.

        The keys of the `results` dictionary are tuples with the substrings,
        matching the respective named wildcards in the `used_names` tuple.
        The values are sets with matching paths.
        """
        return self._results

    @property
    def used_names(self) -> tuple[str, ...]:
        """A tuple of named wildcards present in the pattern."""
        return self._used_names

    @property
    def glob_pattern(self) -> str:
        """The conversion of the named glob to a (more general) conventional glob pattern."""
        return self._glob_pattern

    @property
    def regex(self) -> re.Pattern:
        """The conversion of the named glob to a regular expression."""
        return self._regex

    def _loop_matches(
        self, paths: Iterable[str]
    ) -> Iterator[tuple[tuple[str, ...], set[Path], Path]]:
        """Low-level iterator used by the `extend` and `reduce` methods.

        The paths are tested one by one against the regular expression.
        In case of a hit, it yields a tuple with the following three items:

        - `values`: the substrings matching the named wildcards.
        - `path_set`: the current set of paths associated with the combination of substrings.
        - `path`: a `Path` instance of the matching path.
        """
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
        """Add matching paths from the given list paths.

        Yields
        ------
        values
            A tuple with substring matching the named wildcards,
            only this combination of names was not present yet.
        """
        for values, path_set, path in self._loop_matches(paths):
            if len(path_set) == 0:
                yield values
            path_set.add(path)

    def reduce(self, paths: Iterable[str]) -> Iterator[tuple[str, ...]]:
        """Remove matching paths from given list paths.

        Yields
        ------
        values
            A tuple with deleted substring matching the named wildcards,
            only if the last matching paths were removed.
        """
        for values, path_set, path in self._loop_matches(paths):
            if len(path_set) > 0:
                path_set.discard(path)
                if len(path_set) == 0:
                    yield values

    def glob(self) -> Iterator[tuple[str, ...]]:
        """Extend the results with paths obtained through the built-in glob module.

        Yields
        ------
        values
            A tuple with substring matching the named wildcards,
            only this combination of names was not present yet.
        """
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
    """Iterate over the names of the named wildcards in a Named Glob pattern."""
    for ipart, part in enumerate(RE_NAMED_WILD.split(pattern)):
        if ipart % 2 == 1 and part.startswith("${*"):
            yield part[3:-1]


@attrs.define
class NGlobMulti:
    """A sequence of Named Glob patterns for which consistent matches are collected."""

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
        """Create an instance from the result of the `unstructure` method."""
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
        """Return a serializable representation of the instance."""
        data = [
            [ngs.unstructure(lookup) for ngs in self._nglob_singles],
            self._subs.copy(),
        ]
        # A little hacky way to make the result more compact.
        for ngs_data in data[0]:
            del ngs_data["s"]
        return data

    @classmethod
    def from_patterns(cls, patterns: Iterable[str], subs: dict[str, str] | None = None) -> Self:
        """Create a new instance for given patterns without any results.

        Parameters
        ----------
        patterns
            Named Glob patterns.
            Results will be constrained to have consistently matching substrings
            for the named wildcards appearing in all the patterns.
        subs
            Optional anonymous glob patterns for the named patterns.
            When a name is not present, the wildcard `*` is used for this name.
        """
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
        """The list of NGlobSingle instances, one for each pattern.

        These instances collect (partial) matches before any consistency is imposed between
        the substrings matching the same name in different patterns.
        """
        return self._nglob_singles

    @property
    def patterns(self):
        """The list of Named Glob patterns."""
        return [ngs.pattern for ngs in self._nglob_singles]

    @property
    def subs(self) -> dict[str, str]:
        """User-defined glob patterns for the named wildcards.

        When a name is not present, `*` is used.
        """
        return self._subs

    @property
    def used_names(self) -> tuple[str, ...]:
        """The names used across all the named glob patterns."""
        return self._used_names

    @property
    def has_wildcards(self) -> bool:
        """True if any named or anonymous wildcards are present in the patterns."""
        return self._has_wildcards

    @property
    def results(self) -> dict[tuple[str, ...], list[set[Path]]]:
        """A dictionary with all matches collected so far.

        A key in this dictionary is a tuple of substrings named wildcards,
        using the same order as the `used_names` attribute.

        A value is a list of sets of paths.
        Each item in the list is a set of matching filenames for the corresponding
        pattern from the `patterns` attribute, whose named wildcards match the substrings
        of the key.

        The results can be extended with the `extend` and `glob` methods.
        Conversely, results can be removed with the `reduce` method.
        """
        return self._results

    def _iter_consistent(
        self, criteria: dict[str, str], full_paths: list | int
    ) -> Iterator[tuple[str, ...], list[list[Path]]]:
        """Iterate over (partial) matching substrings and corresponding paths.

        Parameters
        ----------
        criteria
            A dictionary mapping named wildcards to matching substrings.
        full_paths
            If this is a list, it contains lists of paths matching the patterns
            in of the `patterns` attribute with substrings consistent with those in
            the criteria argument.
            Note that this is a recursive iterator, so full_paths may contain fewer
            items than there are patterns when the recursion has not reached it full
            depth yet.
            If this is an integer, it is in index referring to the item in the `patterns`
            to identify the current pattern being processed.
        """
        start = full_paths if isinstance(full_paths, int) else len(full_paths)
        if start == len(self._nglob_singles):
            # We're in the deepest recursion: yield a result.
            yield tuple(criteria[name] for name in self._used_names), full_paths
        else:
            # Recursion in progress...
            ngs = self._nglob_singles[start]
            for new_values, paths in ngs.results.items():
                next_criteria = criteria.copy()
                # Check if named wildcards are consistent with the matching paths so far.
                for name, new_value in zip(ngs.used_names, new_values, strict=False):
                    value = next_criteria.get(name)
                    if value is None:
                        next_criteria[name] = new_value
                    elif value != new_value:
                        # Inconsistent matches for named wildcards in different patterns.
                        # This cannot produce a useful result.
                        next_criteria = None
                        break
                if next_criteria is not None:
                    # Consistency can still be imposed, so enter the next recursion...
                    next_full_paths = (
                        start + 1 if isinstance(full_paths, int) else [*full_paths, paths]
                    )
                    yield from self._iter_consistent(next_criteria, next_full_paths)

    def _extend_consistent(self, i: int, values: tuple[str, ...]):
        """Extend the results of this instance, given an added combination of matching substrings.

        Parameters
        ----------
        i
            The integer index of the pattern in the `patterns` attribute being processed.
        values
            A new set of substrings matching the named wildcards.
        """
        criteria = dict(zip(self._nglob_singles[i].used_names, values, strict=False))
        new_items = list(self._iter_consistent(criteria, []))
        for full_values, full_paths in new_items:
            self._results[full_values] = full_paths

    def _reduce_consistent(self, i: int, values: tuple[str, ...]):
        """Return the results of this instance, given a removed combination of matching substrings.
        Parameters
        ----------
        i
            The integer index of the pattern in the `patterns` attribute being processed.
        values
            A new set of substrings matching the named wildcards.
        """
        criteria = dict(zip(self._nglob_singles[i].used_names, values, strict=False))
        old_items = list(self._iter_consistent(criteria, 0))
        for full_values, _ in old_items:
            del self._results[full_values]

    def extend(self, paths: Iterable[str]):
        """Try to extend the results by searching for matches in the given list of paths."""
        if isinstance(paths, str):
            raise TypeError("The paths argument cannot be a string.")
        for i, ngs in enumerate(self._nglob_singles):
            for values in ngs.extend(paths):
                self._extend_consistent(i, values)

    def reduce(self, paths: Iterable[str]):
        """Drop results by eliminating the provided paths."""
        if isinstance(paths, str):
            raise TypeError("The paths argument cannot be a string.")
        for i, ngs in enumerate(self._nglob_singles):
            for values in ngs.reduce(paths):
                self._reduce_consistent(i, values)

    def glob(self):
        """Extend the results with paths found by the built-in glob function."""
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
        """Iterate over combinations of files that consistently match all patterns.

        This offers a more convenient interface of the `results` attribute.

        Yields
        ------
        nglob_match
            An instance of NGlobMatch, which contains the substrings matching the named wildcards
            and the corresponding lists of paths.
        """
        for values, path_sets in sorted(self._results.items()):
            mapping = dict(zip(self._used_names, values, strict=False))
            files = [
                (sorted(paths) if has_anonymous_wildcards(ngs.pattern) else next(iter(paths)))
                for ngs, paths in zip(self._nglob_singles, path_sets, strict=False)
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
        """True when there are some items in the `results` attribute."""
        return len(self.results) > 0

    def __iter__(self) -> Iterator[str | NGlobMatch]:
        """Iterates over `self.matches` if there are named wildcards, else over `self.files`."""
        if len(self._used_names) > 0:
            return self.matches()
        else:
            return iter(self.files())

    def may_match(self, path):
        """Return True if the path matches one of the NGlobSingle instances.

        This means that it may be path contributing to a consistent match of NGlobMulti.
        When added, it will show up in the result of the `files` method,
        and it may affect the outcome of the `matches` method.
        """
        return any(ngs.regex.fullmatch(path) for ngs in self._nglob_singles)

    def may_change(self, deleted: set[str], updated: set[str]) -> bool:
        """Determine whether the results may change (later) after deleting or adding files.

        Parameters
        ----------
        deleted
            Set of files to be deleted.
        updated
            Set of files to be updated.

        Returns
        -------
        may_change
            True if the NGlobMulti results may change.
            (It may require additional additions and deletions to get any effect,
            but cannot be excluded that the provided deletions and updates play a role in it.)
        """
        updated_new = updated.copy()
        for ngs in self._nglob_singles:
            for paths in ngs.results.values():
                if not deleted.isdisjoint(paths):
                    return True
                updated_new.difference_update(paths)
        for ngs in self._nglob_singles:
            for path in updated_new:
                if ngs.regex.fullmatch(path):
                    return True
        return False

    def will_change(self, deleted: Collection[str], updated: Collection[str]) -> bool:
        """Determine whether the results will change after deleting or adding files.

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
        If a name is not present, `*` is used as default.
    allow_names
        When set to `False`, named wildcards are not allowed.

    Returns
    -------
    regex
        A regular expression string to test if a string matches the pattern.
        It also contains symbolic groups to extract values
        corresponding to named wildcards
        and to impose consistency when the same name appears multiple times.
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
                regex = rf"[^{part[2:-1]}]" if part[1] == "!" else rf"[{part[1:-1]}]"
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
    """Convert nglob wildcards to ordinary ones, compatible with builtin glob and fnmatch modules.

    Parameters
    ----------
    pattern
        A string with named wildcards.
    subs
        A dictionary mapping names to glob patterns.
        If a name is not present, `*` is used as default.

    Returns
    -------
    pattern
        A conventional wildcard string, without the constraint that named wildcards must correspond.
        Where possible, neighboring wildcards are merged into one.
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
