# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Glob with named back-reference support.

Named glob (NGlob) patterns are an advanced form of pattern matching
that supports back-referencing of previously matched substrings.

They have the following use cases:

- **Single named wildcard:**
    By default, the wildcard `${*name}` is a placeholder for any string without a separator,
    just like an anonymous `*`.
    One may also specify a glob pattern for `${*name}` through the `subs` argument.
    For example:

    ```python
    ng = NamedGlob("feedback_${*idx}.md", {"idx": "[0-9][0-9][0-9]"})
    ng.glob()
    print(ng.results)
    ```

    A named wildcard matches the same paths as the anonymous `*` it replaces.
    In particular, it matches a directory,
    without capturing the trailing separator of that directory,
    and it matches an empty string wherever an anonymous `*` does,
    i.e. everywhere except where it makes up a complete path component,
    which can never be empty.
    (`feedback_${*idx}.md` matches `feedback_.md`, but `data/${*idx}` does not match `data/`.)
    Use a substitution that rules this out, e.g. `{"idx": "?*"}`,
    when an empty match is never acceptable.

- **Consistency within one pattern:**
    If a pattern uses the same named wildcard multiple times,
    all its occurrences must match the same substring.
    For example:

    ```python
    ng = NamedGlob("archive_${*idx}/feedback_${*idx}.md", {"idx": "[0-9][0-9][0-9]"})
    ng.glob()
    print(ng.results)
    ```

    These match:

    - `archive_042/feedback_042.md`
    - `archive_777/feedback_777.md`

    This does not match:

    - `archive_042/feedback_777.md`

- Conventional (recursive) glob wildcards are also allowed and are called "anonymous wildcards"
  to distinguish them from named wildcards.

This module is Unix-only:
the forward slash is the one and only path separator it recognizes,
both in patterns and in the paths they are matched against.
"""

import copy
import glob
import re
from collections.abc import Collection, Iterable, Iterator
from typing import Self

import attrs
from path import Path

__all__ = (
    "NamedGlob",
    "NamedGlobMatch",
    "convert_nglob_to_glob",
    "convert_nglob_to_regex",
    "glob_base_dir",
    "has_anonymous_wildcards",
    "has_any_wildcards",
    "has_trailing_recursive_wildcard",
    "iter_wildcard_names",
)

RE_TRAILING_RECURSIVE_WILD_PARTS = [
    r"^[*][*]$",  # recursive ** wildcard, full string
    r"(?<=/)[*][*]$",  # recursive ** wildcard, trailing
]

RE_WILD_PARTS = [
    *RE_TRAILING_RECURSIVE_WILD_PARTS,
    r"^[*][*]/",  # recursive ** wildcard, leading
    r"(?<=/)[*][*]/",  # recursive ** wildcard, middle
    r"\[.*?]",  # anonymous [abc] wildcard
    r"[*]",  # anonymous * wildcard
    r"[?]",  # anonymous ? wildcard
    r"\$\{\*[a-zA-Z0-9_]*?}",  # named wildcard
]

RE_ANY_WILD = re.compile("(" + "|".join(RE_WILD_PARTS) + ")")
RE_TRAILING_RECURSIVE_WILD = re.compile("(" + "|".join(RE_TRAILING_RECURSIVE_WILD_PARTS) + ")")


@attrs.define
class NamedGlobMatch:
    """A single match of a `NamedGlob` pattern, with substrings for its named wildcards.

    The substrings matching the named wildcards can be accessed as attributes.
    For example, the substring matching a named wildcard `foo` is accessed as follows:

    ```python
    print(match.foo)
    ```

    When you expect only a single matching file, use the `single` attribute.
    It will raise an exception when there are zero or multiple matches:

    ```python
    print(match.single)
    ```

    In the unfortunate case that your named wildcards are named `single`, `files`, or `mapping`,
    you can access their values through the `mapping` attribute:

    ```python
    print(match.mapping["single"])
    ```
    """

    _mapping: dict[str, str]
    """Dictionary with `(wildcard_name, substring)` items."""

    _files: Path | list[Path]
    """The matching file(s) for this combination of named-wildcard substrings."""

    def __getattr__(self, name: str) -> str:
        try:
            return self._mapping[name]
        except KeyError as exc:
            raise AttributeError(f"'NamedGlobMatch' object has no attribute '{name}'") from exc

    @property
    def mapping(self) -> dict[str, str]:
        """Dictionary with `(wildcard_name, substring)` items."""
        return self._mapping

    @property
    def files(self) -> Path | list[Path]:
        """The matching file(s) for this combination of named-wildcard substrings.

        This is a single path when the pattern has no anonymous wildcards,
        or a sorted list of paths when it does.
        """
        return self._files

    @property
    def single(self) -> Path:
        """The single matching path.

        Raises
        ------
        ValueError
            If there is not exactly one match.
        """
        result = self._files
        if isinstance(result, list):
            if len(result) == 0:
                raise ValueError("No files matched.")
            if len(result) > 1:
                raise ValueError("Multiple files matched.")
            result = result[0]
        return result


@attrs.define
class NamedGlob:
    """A named glob pattern, matched against a set of paths."""

    _pattern: str = attrs.field()
    """The named glob pattern used to match paths."""

    _subs: dict[str, str] = attrs.field(factory=dict)
    """User-defined glob patterns for the named wildcards."""

    _results: dict[tuple[str, ...], set[Path]] = attrs.field(factory=dict)
    """All matching files, grouped by substrings matching the named wildcards."""

    _used_names: tuple[str, ...] = attrs.field(init=False)
    """Names of the named wildcards used in the pattern, in alphabetical order."""

    _glob_pattern: str = attrs.field(init=False)
    """The equivalent conventional glob pattern, without back-references."""

    _regex: re.Pattern[str] = attrs.field(init=False)
    """The equivalent regular expression, with symbolic groups for the named wildcards."""

    @_used_names.default
    def _default_used_names(self) -> tuple[str, ...]:
        return tuple(sorted(set(iter_wildcard_names(self._pattern))))

    @_glob_pattern.default
    def _default_glob(self) -> str:
        return convert_nglob_to_glob(self._pattern, self._subs)

    @_regex.default
    def _default_regex(self) -> re.Pattern[str]:
        return re.compile(convert_nglob_to_regex(self._pattern, self._subs))

    @property
    def pattern(self) -> str:
        """The named glob pattern used to match paths."""
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

        The keys are tuples with the substrings matching the named wildcards of the pattern,
        in alphabetical order of the wildcard names.
        The values are sets with matching paths.
        """
        return self._results

    def _match_values(self, path: str) -> tuple[str, ...] | None:
        """Return the substrings matching the named wildcards, or `None` if there is no match."""
        match_ = self._regex.fullmatch(path)
        if match_ is None:
            return None
        mapping = match_.groupdict()
        return tuple(mapping[name] for name in self._used_names)

    def extend(self, paths: Iterable[str]) -> None:
        """Add the paths matching the pattern to the results, ignoring the others."""
        for path in paths:
            values = self._match_values(path)
            if values is not None:
                self._results.setdefault(values, set()).add(Path(path))

    def reduce(self, paths: Iterable[str]) -> None:
        """Remove the given paths from the results, ignoring those that are not present."""
        for path in paths:
            values = self._match_values(path)
            if values is not None:
                path_set = self._results.get(values)
                if path_set is not None:
                    path_set.discard(Path(path))
                    if len(path_set) == 0:
                        del self._results[values]

    def glob(self) -> None:
        """Extend the results with paths obtained through Python's built-in glob module.

        Directories are added with a trailing separator,
        consistent with how StepUp represents directory paths elsewhere.
        """
        paths = []
        for path in glob.iglob(self._glob_pattern, recursive=True, include_hidden=True):
            path = Path(path)
            if path.is_dir():
                path = path / ""
            paths.append(path)
        self.extend(paths)

    def will_change(self, deleted: Collection[str], added: Collection[str]) -> Self | None:
        """Determine whether the results will change after deleting or adding files.

        Parameters
        ----------
        deleted
            Files to be deleted.
        added
            Files to be added.
            A file present in both `added` and `deleted` ends up deleted.

        Returns
        -------
        evolved
            A new copy with the changes applied,
            or `None` when the results are unaffected.
        """
        evolved = copy.deepcopy(self)
        evolved.extend(added)
        evolved.reduce(deleted)
        return None if evolved._results == self._results else evolved

    # Convenience methods

    def matches(self) -> Iterator[NamedGlobMatch]:
        """Iterate over the matches, grouped by consistent named-wildcard substrings.

        This offers a more convenient interface to the `results` attribute.

        Yields
        ------
        named_glob_match
            A `NamedGlobMatch` instance,
            which contains the substrings matching the named wildcards
            and the corresponding matching file(s).
        """
        has_anon = has_anonymous_wildcards(self._pattern)
        for values, paths in sorted(self._results.items()):
            mapping = dict(zip(self._used_names, values, strict=True))
            files = sorted(paths) if has_anon else next(iter(paths))
            yield NamedGlobMatch(mapping, files)

    def files(self) -> list[Path]:
        """Return a sorted list of all paths matching the pattern, without duplicates."""
        result = set()
        for path_set in self._results.values():
            result.update(path_set)
        return sorted(result)

    def single(self) -> Path:
        """Return the single matching path.

        Raises
        ------
        ValueError
            If there is not exactly one match.
        """
        files = self.files()
        if len(files) != 1:
            raise ValueError(f"There are {len(files)} matches, not just one.")
        return files[0]

    def __bool__(self) -> bool:
        """True when the `results` attribute is not empty."""
        return len(self._results) > 0

    def __iter__(self) -> Iterator[Path | NamedGlobMatch]:
        """Iterate over `self.matches()` if there are named wildcards, else over `self.files()`."""
        if len(self._used_names) > 0:
            return self.matches()
        return iter(self.files())


def has_any_wildcards(pattern: str) -> bool:
    """Test if a glob pattern has anonymous or named wildcards."""
    return RE_ANY_WILD.search(pattern) is not None


def has_trailing_recursive_wildcard(pattern: str) -> bool:
    """Test if a glob pattern ends with a recursive `**` wildcard.

    True when the pattern is exactly `**`, or its last path component is `**` (e.g. `src/**`).
    A `**` earlier in the pattern (leading or middle, e.g. `**/src/x` or `src/**/x`) does not count.
    """
    return RE_TRAILING_RECURSIVE_WILD.search(pattern) is not None


def has_anonymous_wildcards(pattern: str) -> bool:
    """Test if a glob pattern has anonymous wildcards."""
    for ipart, part in enumerate(RE_ANY_WILD.split(pattern)):
        if ipart % 2 == 1 and not part.startswith("${*"):
            return True
    return False


def iter_wildcard_names(pattern: str) -> Iterator[str]:
    """Iterate over the names of the named wildcards in a named glob pattern.

    Raises
    ------
    ValueError
        If a named wildcard has an empty name.
    """
    for ipart, part in enumerate(RE_ANY_WILD.split(pattern)):
        if ipart % 2 == 1 and part.startswith("${*"):
            yield _get_wildcard_name(part, pattern)


def _get_wildcard_name(part: str, pattern: str) -> str:
    """Extract the name from a `${*name}` fragment of a pattern.

    Parameters
    ----------
    part
        A fragment of the form `${*name}`.
    pattern
        The complete pattern, only used in the error message.

    Returns
    -------
    name
        The name of the wildcard.

    Raises
    ------
    ValueError
        If the name is empty.
    """
    name = part[3:-1]
    if len(name) == 0:
        raise ValueError(f"A named wildcard must have a name, got '${{*}}' in {pattern}")
    return name


def convert_nglob_to_regex(
    pattern: str, subs: dict[str, str] | None = None, allow_names: bool = True
) -> str:
    """Convert a named glob pattern to a regular expression.

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

    Raises
    ------
    ValueError
        If the pattern is empty,
        if it contains a named wildcard while `allow_names` is `False`,
        if a named wildcard has an empty name,
        or if a wildcard cannot be converted.
    """
    if len(pattern) == 0:
        raise ValueError("Cannot convert an empty pattern to a regular expression.")
    if subs is None:
        subs = {}
    parts = []
    # Last non-empty fragment (text or wildcard) seen so far,
    # used to merge neighboring anonymous wildcards.
    last = None
    # Names of the named wildcards encountered so far.
    # A name seen twice becomes a back-reference instead of a new group.
    encountered = set()
    # Positions in `parts` of the named groups that expand to `[^/]*`,
    # i.e. those matching a single path component, mapped to their name.
    # The post-processing below treats them exactly like a bare `*`,
    # so that replacing `*` by `${*name}` does not change which paths match.
    star_names = {}
    for i, part in enumerate(RE_ANY_WILD.split(pattern)):
        if i % 2 == 0:
            if len(part) > 0:
                # Not a wildcard: escape regex characters.
                parts.append(re.escape(part))
        else:
            # A (named) wildcard: replace with corresponding regex.
            replace = False
            regex = None
            star_name = None
            if part == "?":
                regex = r"[^/]"
            elif part == "*":
                if last not in ["*", "**"]:
                    regex = r"[^/]*"
            elif part == "**":
                if last != "**":
                    regex = r".*"
                    if last == "*":
                        replace = True
            elif part == "**/":
                if last != "**/":
                    regex = r"(?:.*/|)"
                    if last in ["*", "**"]:
                        replace = True
            elif part.startswith("[") and part.endswith("]"):
                regex = rf"[^{part[2:-1]}]" if part[1] == "!" else rf"[{part[1:-1]}]"
            elif part.startswith("${*") and part.endswith("}"):
                if not allow_names:
                    raise ValueError(f"Named wildcards not allowed in {pattern}")
                name = _get_wildcard_name(part, pattern)
                if name in encountered:
                    regex = rf"(?P={name})"
                else:
                    part_regex = convert_nglob_to_regex(subs.get(name, "*"), {}, False)
                    regex = rf"(?P<{name}>{part_regex})"
                    encountered.add(name)
                    if part_regex == r"[^/]*":
                        star_name = name
            else:
                raise ValueError(f"Cannot convert wildcard to regex: {part}")
            if regex is not None and len(regex) > 0:
                if replace:
                    parts[-1] = regex
                else:
                    parts.append(regex)
                if star_name is not None:
                    star_names[len(parts) - 1] = star_name
        if len(part) > 0:
            last = part

    if allow_names:
        # Post-process the wildcards matching a single path component,
        # both the anonymous ones and the named ones listed in `star_names`.
        # Such a wildcard may not match an empty string when it makes up a complete component,
        # because a path never contains an empty component.
        # - The enclosed case: a separator on either side.
        for ipart, part in enumerate(parts):
            if not (
                ipart > 0
                and ipart < len(parts) - 1
                and parts[ipart - 1].endswith("/")
                and parts[ipart + 1].startswith("/")
            ):
                continue
            star_name = star_names.get(ipart)
            if star_name is not None:
                parts[ipart] = rf"(?P<{star_name}>[^/]+)"
            elif part.endswith("*"):
                parts[ipart] = f"{part[:-1]}+"
        # - The trailing case: the pattern ends with the wildcard,
        #   which must then also match paths with a trailing separator.
        #   The separator stays outside the named group,
        #   so a named wildcard never captures a trailing separator.
        star_name = star_names.get(len(parts) - 1)
        if star_name is not None or parts[-1] == r"[^/]*":
            body = r"[^/]+" if len(parts) >= 2 and parts[-2].endswith("/") else r"[^/]*"
            parts[-1] = rf"(?P<{star_name}>{body})" if star_name is not None else body
            parts.append("/?")

    return "".join(parts)


def convert_nglob_to_glob(pattern: str, subs: dict[str, str] | None = None) -> str:
    """Convert nglob wildcards to ordinary ones, compatible with `glob` and `fnmatch`.

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
        A conventional wildcard string,
        without the constraint that repeated named wildcards must match the same substring.
        Where possible, neighboring wildcards are merged into one.

    Raises
    ------
    ValueError
        If a named wildcard has an empty name.
    """
    if subs is None:
        subs = {}
    # Split into text, wildcard and named-wildcard fragments.
    parts = []
    # The odd-numbered indices match a (named) wildcard.
    for i, part in enumerate(RE_ANY_WILD.split(pattern)):
        if i % 2 == 1 and part.startswith("${*"):
            # Split the substituted named wildcards once more.
            parts.extend(RE_ANY_WILD.split(subs.get(_get_wildcard_name(part, pattern), "*")))
        else:
            # No substitution, so no additional splitting required.
            parts.append(part)
    # Remove empty strings due to neighboring wildcards with no normal text in between.
    parts = [part for part in parts if part != ""]
    # Merge asterisks that would otherwise be glued together,
    # and apply a few other simplifications.
    texts = []
    for part in parts:
        if len(texts) == 0 or part == "?":
            texts.append(part)
        elif part == "*":
            if texts[-1] not in ["*", "**"]:
                texts.append("*")
        elif part == "**":
            if texts[-1] == "*":
                texts[-1] = "**"
            elif texts[-1] != "**":
                texts.append("**")
        elif part == "**/":
            if texts[-1] in ["*", "**"]:
                texts[-1] = "**/"
            elif texts[-1] != "**/":
                texts.append("**/")
        else:
            texts.append(part)
    return "".join(texts)


def glob_base_dir(pattern: str) -> str:
    """Return the longest wildcard-free directory prefix of a glob pattern.

    The result never has a trailing separator,
    and is `.` when the first path component already contains a wildcard.

    Parameters
    ----------
    pattern
        A (named) glob pattern, relative to the project root.

    Returns
    -------
    base_dir
        The directory below which every match of `pattern` must lie.
    """
    parts = []
    for part in pattern.split("/")[:-1]:
        if has_any_wildcards(part):
            break
        parts.append(part)
    return "/".join(parts) if parts else "."
