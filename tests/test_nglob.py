# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Unit tests for stepup.core.nglob2"""

import contextlib
import re
from collections.abc import Collection

import pytest
from path import Path

from stepup.core.nglob import (
    RE_ANY_WILD,
    NamedGlob,
    NamedGlobMatch,
    convert_nglob_to_glob,
    convert_nglob_to_regex,
    has_anonymous_wildcards,
    has_any_wildcards,
    iter_wildcard_names,
)


@pytest.mark.parametrize(
    "pattern",
    [
        "bar_${*foo}",
        "foo*",
        "*",
        "?",
        "**",
        "ls/ff**f/",
        "**/*.txt",
        "data/**",
        "data/**/*",
        "data/**/out.txt",
        "num[0-9]",
    ],
)
def test_has_any_wildcards_true(pattern):
    assert has_any_wildcards(pattern)


@pytest.mark.parametrize("pattern", ["[aaa", "blub"])
def test_has_any_wildcards_false(pattern):
    assert not has_any_wildcards(pattern)


@pytest.mark.parametrize(
    "pattern", ["foo*", "*", "?", "**", "ls/ff**f/", "./**/help.txt", "num[0-9]"]
)
def test_has_anonymous_wildcards_true(pattern):
    assert has_anonymous_wildcards(pattern)


@pytest.mark.parametrize("pattern", ["bar_${*foo}", "[aaa", "blub"])
def test_has_anonymous_wildcards_false(pattern):
    assert not has_anonymous_wildcards(pattern)


@pytest.mark.parametrize(
    ("pattern", "names"),
    [
        ("bar_${*foo}", ["foo"]),
        ("bar_*foo", []),
        ("{*bar}_$*foo", []),
        ("${*bar}_${*foo}", ["bar", "foo"]),
    ],
)
def test_iter_wildcard_names(pattern, names):
    assert list(iter_wildcard_names(pattern)) == names


@pytest.mark.parametrize(
    "call",
    [
        lambda pattern: list(iter_wildcard_names(pattern)),
        convert_nglob_to_regex,
        convert_nglob_to_glob,
        NamedGlob,
    ],
)
def test_empty_wildcard_name(call):
    with pytest.raises(ValueError, match="must have a name"):
        call("bar_${*}.txt")


def test_empty_pattern_to_regex():
    with pytest.raises(ValueError, match="empty pattern"):
        convert_nglob_to_regex("")


def test_named_glob_simple(tmpdir):
    pattern = "path/${*prefix}/foo*/${*prefix}-main.txt"
    ng = NamedGlob(pattern, {})
    assert ng.pattern == pattern
    assert ng.subs == {}
    assert ng.used_names == ("prefix",)
    ng.extend(
        [
            "path/some/foo1/some-main.txt",
            "path/other/foo1/other-main.txt",
            "path/other/foo2/other-main.txt",
            "path/other/foo1/some-main.txt",
        ]
    )
    assert ng.results == {
        ("other",): {
            "path/other/foo1/other-main.txt",
            "path/other/foo2/other-main.txt",
        },
        ("some",): {"path/some/foo1/some-main.txt"},
    }
    ng.reduce(["path/some/foo1/some-main.txt"])
    assert ng.results == {
        ("other",): {
            "path/other/foo1/other-main.txt",
            "path/other/foo2/other-main.txt",
        },
    }
    with contextlib.chdir(tmpdir):
        _make_files(
            [
                "path/blub/foo1/blub-main.txt",
                "path/blub/foo1/other-main.txt",
                "path/blub/foo2/blub-main.txt",
                "path/blub/foo3/other-main.txt",
            ]
        )
        ng.glob()
    assert ng.results == {
        ("other",): {
            "path/other/foo1/other-main.txt",
            "path/other/foo2/other-main.txt",
        },
        ("blub",): {"path/blub/foo1/blub-main.txt", "path/blub/foo2/blub-main.txt"},
    }


def test_named_glob_simple_subs():
    pattern = "path/${*prefix}/foo${*num}/${*prefix}-main.txt"
    subs = {"num": "[0-9]"}
    ng = NamedGlob(pattern, subs)
    assert ng.used_names == ("num", "prefix")
    ng.extend(
        [
            "path/some/foo1/some-main.txt",
            "path/other/foo1/other-main.txt",
            "path/other/foo2/other-main.txt",
            "path/other/foo_/other-main.txt",
            "path/other/foo1/some-main.txt",
        ]
    )
    assert ng.results == {
        ("1", "some"): {"path/some/foo1/some-main.txt"},
        ("1", "other"): {"path/other/foo1/other-main.txt"},
        ("2", "other"): {"path/other/foo2/other-main.txt"},
    }


def test_named_glob_anonymous():
    paths = [
        "path/some/foo1/some-main.txt",
        "path/other/foo1/other-main.txt",
        "path/other/foo2/other-main.txt",
        "path/other/foo1/some-main.txt",
    ]
    pattern = "path/*/foo*/*-main.txt"
    subs = {}
    ng = NamedGlob(pattern, {})
    assert ng.pattern == pattern
    assert ng.subs == subs
    assert ng.used_names == ()
    ng.extend(paths)
    assert ng.results == {(): set(paths)}


@pytest.mark.parametrize(
    ("pattern", "subs"),
    [
        ("inp*.txt", {}),
        ("${*inp}.txt", {}),
        ("${*inp}.txt", {"inp": "???"}),
    ],
)
def test_named_glob_has_wildcards_true(pattern, subs):
    assert NamedGlob(pattern, subs).can_match_multiple


@pytest.mark.parametrize(
    ("pattern", "subs"),
    [
        ("inp.txt", {}),
        ("${inp}.txt", {}),
        ("${*inp}.txt", {"inp": "foo"}),
    ],
)
def test_named_glob_has_wildcards_false(pattern, subs):
    assert not NamedGlob(pattern, subs).can_match_multiple


def test_named_glob_iterators_anonymous():
    ng = NamedGlob("pre_*.txt")
    assert ng.can_match_multiple
    assert len(ng.used_names) == 0

    # Add a few things and test
    ng.extend(["pre_fir.txt", "pre_sec.txt", "other.log"])
    for files in ng.files(), list(ng):
        assert files == ["pre_fir.txt", "pre_sec.txt"]
    matches = list(ng.matches())
    assert len(matches) == 1
    match = matches[0]
    assert match.mapping == {}
    assert match.files == ["pre_fir.txt", "pre_sec.txt"]
    with pytest.raises(AttributeError):
        _ = match.anything

    assert ng.may_change({"pre_fir.txt"}, set())
    assert ng.may_change(set(), {"pre_foo.txt"})
    assert not ng.may_change({"other.log"}, set())
    assert not ng.may_change(set(), {"pre_fir.txt"})

    ng.reduce(["pre_sec.txt"])
    for files in ng.files(), list(ng):
        assert files == ["pre_fir.txt"]
    matches = list(ng.matches())
    assert len(matches) == 1
    match = matches[0]
    assert match.mapping == {}
    assert match.files == ["pre_fir.txt"]

    assert ng.will_change({"pre_fir.txt"}, set()) is not None
    assert ng.will_change(set(), {"pre_foo.txt"}) is not None
    assert ng.will_change({"other.log"}, set()) is None
    assert ng.will_change(set(), {"pre_fir.txt"}) is None


def test_named_glob_single_named():
    subs = {"inp": "prefix_*"}
    ng = NamedGlob("${*inp}.txt", subs)
    assert ng.subs is subs
    ng.extend(["prefix_a.pdf", "prefix_b.txt"])
    assert ng.files() == ["prefix_b.txt"]
    assert next(iter(ng.matches())).single == "prefix_b.txt"
    assert ng.single() == "prefix_b.txt"


def test_named_glob_single_anonymous():
    ng = NamedGlob("*.txt")
    ng.extend(["prefix_a.pdf", "prefix_b.txt"])
    assert ng.files() == ["prefix_b.txt"]
    assert next(iter(ng.matches())).single == "prefix_b.txt"
    assert ng.single() == "prefix_b.txt"


@pytest.mark.parametrize(
    ("string", "matches"),
    [
        ("foo*", ["*"]),
        ("foo**", ["*", "*"]),
        ("foo${*bar}", ["${*bar}"]),
        ("*foo${*bar}", ["*", "${*bar}"]),
        ("***foo${*bar}", ["*", "*", "*", "${*bar}"]),
        ("**spam*foo${*bar}", ["*", "*", "*", "${*bar}"]),
        ("*spam**foo${*bar}", ["*", "*", "*", "${*bar}"]),
        ("*${*spam}**foo${*bar}", ["*", "${*spam}", "*", "*", "${*bar}"]),
        ("*foo?", ["*", "?"]),
        ("?foo??", ["?", "?", "?"]),
        ("?foo[ab]?", ["?", "[ab]", "?"]),
        ("?foo[a-z][0-9][^?][?]?", ["?", "[a-z]", "[0-9]", "[^?]", "[?]", "?"]),
        ("foo[?]", ["[?]"]),
        ("foo[*]", ["[*]"]),
        ("foo[${*ab}]", ["[${*ab}]"]),
        ("foo[[]a]", ["[[]"]),
        ("**/", ["**/"]),
        ("/**", ["**"]),
        ("**", ["**"]),
        ("./**/*.txt", ["**/", "*"]),
        ("data/**/*.txt", ["**/", "*"]),
    ],
)
def test_nglob_wild(string, matches):
    assert re.findall(RE_ANY_WILD, string) == matches


@pytest.mark.parametrize(
    ("pattern", "normal"),
    [
        ("generic/${*ch}/*.md", "generic/*/*.md"),
        ("generic/*${*ch}/*.md", "generic/*/*.md"),
        ("generic/${*ch}*/*.md", "generic/*/*.md"),
        ("generic/*${*ch}*/*.md", "generic/*/*.md"),
        ("generic/*${*ch}**/*.md", "generic/*/*.md"),
        ("generic/**${*ch}*/*.md", "generic/*/*.md"),
        ("generic/**${*ch}**/*.md", "generic/*/*.md"),
        ("generic/${*ch}${*foo}/*.md", "generic/*/*.md"),
        ("generic/${*ch}-${*foo}/*.md", "generic/*-*/*.md"),
        ("generic/${*ch}/${*foo}/*.md", "generic/*/*/*.md"),
        ("${*generic}/ch${*foo}/*.md", "*/ch*/*.md"),
        ("generic/ch${*foo}/${*md}", "generic/ch*/*"),
        ("generic/${*md}${*ch}/${*md}", "generic/*/*"),
        ("generic/${*md}?/${*md}", "generic/*?/*"),
        ("generic/**?/?${*md}", "generic/*?/?*"),
        ("generic/?**/*?", "generic/?*/*?"),
        ("generic/**/*?", "generic/**/*?"),
        ("generic/${*md}[a[b]/?[*]", "generic/*[a[b]/?[*]"),
        ("**/${*name}.txt", "**/*.txt"),
        ("foo/**/${*name}.txt", "foo/**/*.txt"),
        ("${*sub}/**", "*/**"),
        ("${*sub}/**/", "*/**/"),
        ("data**", "data*"),
        ("data/**/", "data/**/"),
        ("data/**/*.txt", "data/**/*.txt"),
    ],
)
def test_nglob_to_glob(pattern, normal):
    assert convert_nglob_to_glob(pattern) == normal


@pytest.mark.parametrize(
    ("pattern", "subs", "normal"),
    [
        (
            "${*generic}/${*ch}/*.md",
            {"generic": "?[ab]*", "ch": "s_*_*"},
            "?[ab]*/s_*_*/*.md",
        ),
        ("${*a}${*b}/ab", {"a": "a*"}, "a*/ab"),
        ("${*a}${*b}/ab", {"a": "a*", "b": "?b"}, "a*?b/ab"),
        ("${*a}${*b}${*a}/ab", {"a": "?a*", "b": "**b*"}, "?a*b*?a*/ab"),
        ("${*a}/ab", {"a": "**/*a"}, "**/*a/ab"),
    ],
)
def test_nglob_to_glob_subs(pattern, subs, normal):
    assert convert_nglob_to_glob(pattern, subs) == normal


@pytest.mark.parametrize(
    ("pattern", "regex"),
    [
        ("generic/${*ch}/*.md", r"generic/(?P<ch>[^/]+)/[^/]*\.md"),
        ("generic/${*ch}/?.md", r"generic/(?P<ch>[^/]+)/[^/]\.md"),
        ("generic/${*ch}/[abc].md", r"generic/(?P<ch>[^/]+)/[abc]\.md"),
        ("generic/${*ch}/[!abc].md", r"generic/(?P<ch>[^/]+)/[^abc]\.md"),
        (
            "generic/${*ch}${*foo}/*.md",
            r"generic/(?P<ch>[^/]*)(?P<foo>[^/]*)/[^/]*\.md",
        ),
        (
            "generic/${*ch}-${*foo}/*.md",
            r"generic/(?P<ch>[^/]*)\-(?P<foo>[^/]*)/[^/]*\.md",
        ),
        (
            "generic/${*ch}/${*foo}/*.md",
            r"generic/(?P<ch>[^/]+)/(?P<foo>[^/]+)/[^/]*\.md",
        ),
        (
            "generic/${*ch}**${*foo}/*.md",
            r"generic/(?P<ch>[^/]*)[^/]*(?P<foo>[^/]*)/[^/]*\.md",
        ),
        (
            "generic/${*ch}**/${*foo}/*.md",
            r"generic/(?P<ch>[^/]*)[^/]*/(?P<foo>[^/]+)/[^/]*\.md",
        ),
        (
            "${*generic}/ch${*foo}/*.md",
            r"(?P<generic>[^/]*)/ch(?P<foo>[^/]*)/[^/]*\.md",
        ),
        ("generic/ch${*foo}/${*md}", r"generic/ch(?P<foo>[^/]*)/(?P<md>[^/]+)/?"),
        ("generic/${*md}${*ch}/${*md}", r"generic/(?P<md>[^/]*)(?P<ch>[^/]*)/(?P=md)"),
        ("data**", r"data[^/]*/?"),
        ("data/**/", r"data/(?:.*/|)"),
        ("data/**/*.txt", r"data/(?:.*/|)[^/]*\.txt"),
    ],
)
def test_nglob_to_regex(pattern, regex):
    assert convert_nglob_to_regex(pattern) == regex


@pytest.mark.parametrize(
    ("pattern", "subs", "regex"),
    [
        (
            "prefix_${*year}",
            {"year": "[0-9][0-9][0-9][0-9]"},
            r"prefix_(?P<year>[0-9][0-9][0-9][0-9])",
        ),
        (
            "latex-${*name}/${*name}.tex",
            {"name": "?*"},
            r"latex\-(?P<name>[^/][^/]*)/(?P=name)\.tex",
        ),
    ],
)
def test_nglob_to_regex_subs(pattern, subs, regex):
    assert convert_nglob_to_regex(pattern, subs) == regex


def test_nglob_to_regex_groups():
    regex = re.compile(convert_nglob_to_regex("generic/${*ch}/**/${*foo}/*.md"))
    match_ = regex.fullmatch("generic/ch1/some/some/name/file.md")
    assert match_.groups() == ("ch1", "name")


@pytest.mark.parametrize(
    ("anonymous", "named"),
    [
        ("*", "${*x}"),
        ("*/", "${*x}/"),
        ("sub/*", "sub/${*x}"),
        ("sub/*/", "sub/${*x}/"),
        ("sub/*/deep", "sub/${*x}/deep"),
    ],
)
def test_named_wildcard_matches_like_anonymous(tmpdir, anonymous, named):
    """Replacing an anonymous `*` by a named wildcard must not change which paths match.

    Without this, a named wildcard silently drops directory matches,
    because only the anonymous `*` allowed a trailing separator.
    """
    paths = ["sub/", "sub/file", "sub/other/", "sub/other/deep"]
    # Take the matches from a list of paths.
    ng_list_anonymous = NamedGlob(anonymous)
    ng_list_anonymous.extend(paths)
    ng_list_named = NamedGlob(named)
    ng_list_named.extend(paths)
    # Take the matches from the file system.
    with contextlib.chdir(tmpdir):
        _make_files(paths)
        ng_glob_anonymous = NamedGlob(anonymous)
        ng_glob_anonymous.glob()
        ng_glob_named = NamedGlob(named)
        ng_glob_named.glob()
    # The named wildcard must agree with the anonymous one,
    # and both ways of collecting matches must agree with each other.
    expected = ng_list_anonymous.files()
    assert len(expected) > 0
    assert ng_list_named.files() == expected
    assert ng_glob_anonymous.files() == expected
    assert ng_glob_named.files() == expected


def test_named_wildcard_excludes_trailing_separator(tmpdir):
    """A named wildcard matches a directory without capturing its trailing separator."""
    with contextlib.chdir(tmpdir):
        _make_files(["sub/file", "sub/other/"])
        ng = NamedGlob("sub/${*x}")
        ng.glob()
    assert ng.results == {("file",): {"sub/file"}, ("other",): {"sub/other/"}}
    assert [(match.x, match.single) for match in ng.matches()] == [
        ("file", Path("sub/file")),
        ("other", Path("sub/other/")),
    ]


def _make_files(paths: Collection[str]):
    for path in paths:
        path = Path(path)
        if path.endswith("/"):
            path.makedirs_p()
        else:
            if len(path.parent) > 0:
                path.parent.makedirs_p()
            with open(path, "w"):
                pass


def _check_named_glob(tmpdir, pattern, subs, paths, used_names, results):
    with contextlib.chdir(tmpdir):
        _make_files(paths)
        ng1 = NamedGlob(pattern, subs)
        ng1.glob()
        assert ng1.results == results
    ng2 = NamedGlob(pattern, subs)
    assert ng2.used_names == used_names
    assert ng2.subs == subs
    assert ng2.can_match_multiple
    ng2.extend(paths)
    assert ng2.results == results
    assert bool(ng2) == (len(results) > 0)
    return ng2


def test_named_glob_nowildcards():
    ng = NamedGlob("inp1.txt")
    ng.extend(["inp1.txt", "foo.bar"])
    assert ng.results == {(): {"inp1.txt"}}
    items = list(ng)
    assert len(items) == 1
    assert isinstance(items[0], str)


def test_named_glob_nonames():
    ng = NamedGlob("*.log")
    ng.extend(["inp.txt", "foo.bar", "worker.log", "director.log"])
    assert ng.results == {(): {"director.log", "worker.log"}}
    assert list(ng) == ["director.log", "worker.log"]


def test_named_glob_subdirs():
    ng = NamedGlob("sub/*")
    ng.extend(["not", "nono/", "sub/", "sub/file", "sub/other/"])
    assert ng.results == {(): {"sub/file", "sub/other/"}}
    assert list(ng) == ["sub/file", "sub/other/"]


def test_named_glob_repeated_name():
    ng = NamedGlob("prefix_${*f}_${*f}.txt")
    ng.extend(["prefix_a_b.txt", "prefix_b_a.txt", "prefix_b_b.txt", "prefix_a_a.txt"])
    assert ng.results == {("a",): {"prefix_a_a.txt"}, ("b",): {"prefix_b_b.txt"}}
    assert list(ng) == [
        NamedGlobMatch({"f": "a"}, Path("prefix_a_a.txt")),
        NamedGlobMatch({"f": "b"}, Path("prefix_b_b.txt")),
    ]


def test_named_glob_named_empty():
    ng = NamedGlob("prefix_${*f}.txt")
    ng.extend(["prefix_.txt", "prefix_a.txt", "prefix_b.txt"])
    assert ng.results == {
        ("",): {"prefix_.txt"},
        ("a",): {"prefix_a.txt"},
        ("b",): {"prefix_b.txt"},
    }
    assert list(ng) == [
        NamedGlobMatch({"f": ""}, Path("prefix_.txt")),
        NamedGlobMatch({"f": "a"}, Path("prefix_a.txt")),
        NamedGlobMatch({"f": "b"}, Path("prefix_b.txt")),
    ]


def test_named_glob_named_not_empty():
    ng = NamedGlob("prefix_${*f}.txt", subs={"f": "?*"})
    ng.extend(["prefix_.txt", "prefix_a.txt", "prefix_b.txt"])
    assert ng.results == {("a",): {"prefix_a.txt"}, ("b",): {"prefix_b.txt"}}
    assert list(ng) == [
        NamedGlobMatch({"f": "a"}, Path("prefix_a.txt")),
        NamedGlobMatch({"f": "b"}, Path("prefix_b.txt")),
    ]


def test_named_glob_named_ext(tmpdir):
    pattern = "../../general/${*name}-public${*ext}"
    paths = ["../../general/.gitignore-public", "../../general/.pre-commit-config-public.yaml"]
    subs = {}
    used_names = ("ext", "name")
    results = {
        ("", ".gitignore"): {"../../general/.gitignore-public"},
        (".yaml", ".pre-commit-config"): {"../../general/.pre-commit-config-public.yaml"},
    }
    _check_named_glob(tmpdir, pattern, subs, paths, used_names, results)


def test_recursive1(tmpdir):
    _check_named_glob(
        tmpdir,
        pattern="data/**",
        subs={},
        paths=["data/", "data/sub/", "data/sub/part1.txt", "data/part2.txt"],
        used_names=(),
        results={(): {"data/", "data/sub/", "data/sub/part1.txt", "data/part2.txt"}},
    )


def test_recursive2(tmpdir):
    _check_named_glob(
        tmpdir,
        pattern="data**",
        subs={},
        paths=["data/", "data/sub/", "data/sub/part1.txt", "data/part2.txt"],
        used_names=(),
        results={(): {"data/"}},
    )


def test_recursive3(tmpdir):
    _check_named_glob(
        tmpdir,
        pattern="data**/",
        subs={},
        paths=["data/", "data/sub/", "data/sub/part1.txt", "data/part2.txt"],
        used_names=(),
        results={(): {"data/"}},
    )


def test_recursive4(tmpdir):
    _check_named_glob(
        tmpdir,
        pattern="**.txt",
        subs={},
        paths=["data/", "data/sub/", "data/sub/part1.txt", "data/part2.txt"],
        used_names=(),
        results={},
    )


def test_recursive5(tmpdir):
    _check_named_glob(
        tmpdir,
        pattern="**/*.txt",
        subs={},
        paths=["data/", "data/sub/", "data/sub/part1.txt", "data/part2.txt"],
        used_names=(),
        results={(): {"data/sub/part1.txt", "data/part2.txt"}},
    )


def test_recursive6(tmpdir):
    _check_named_glob(
        tmpdir,
        pattern="**/",
        subs={},
        paths=["data/", "data/sub/", "data/sub/part1.txt", "data/part2.txt"],
        used_names=(),
        results={(): {"data/", "data/sub/"}},
    )


def test_recursive7(tmpdir):
    _check_named_glob(
        tmpdir,
        pattern="data/**/*.txt",
        subs={},
        paths=["data.txt", "data/sub/part1.txt", "data/part2.txt"],
        used_names=(),
        results={(): {"data/sub/part1.txt", "data/part2.txt"}},
    )


def test_hidden(tmpdir):
    _check_named_glob(
        tmpdir,
        pattern="*.txt",
        subs={},
        paths=["visible.txt", ".hidden.txt"],
        used_names=(),
        results={(): {"visible.txt", ".hidden.txt"}},
    )


def test_named_glob_will_change():
    ng = NamedGlob("subdir*/")
    assert not ng.may_change(set(), {"subdir/foo.txt"})
    assert not ng.may_change(set(), {"foo.log"})
    assert ng.may_change(set(), {"subdir1/"})

    assert ng.will_change(set(), {"subdir/foo.txt"}) is None
    assert ng.will_change(set(), {"foo.log"}) is None
    assert ng.will_change(set(), {"subdir1/"}) is not None
