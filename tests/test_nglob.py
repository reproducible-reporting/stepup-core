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
"""Unit tests for stepup.core.nglob2"""

import contextlib
import re
from collections.abc import Collection

import msgpack
import pytest
from path import Path

from stepup.core.nglob import (
    RE_NAMED_WILD,
    NGlobMatch,
    NGlobMulti,
    NGlobSingle,
    convert_nglob_to_glob,
    convert_nglob_to_regex,
    has_anonymous_wildcards,
    has_wildcards,
    iter_wildcard_names,
)
from stepup.core.utils import lookupdict


@pytest.mark.parametrize(
    "pattern", ["bar_${*foo}", "foo*", "*", "?", "**", "ls/ff**f/", "num[0-9]"]
)
def test_has_wildcards_true(pattern):
    assert has_wildcards(pattern)


@pytest.mark.parametrize("pattern", ["[aaa", "blub"])
def test_has_wildcards_false(pattern):
    assert not has_wildcards(pattern)


@pytest.mark.parametrize("pattern", ["foo*", "*", "?", "**", "ls/ff**f/", "num[0-9]"])
def test_has_anonymous_wildcards_true(pattern):
    assert has_anonymous_wildcards(pattern)


@pytest.mark.parametrize("pattern", ["bar_${*foo}", "[aaa", "blub"])
def test_has_anonymous_wildcards_false(pattern):
    assert not has_anonymous_wildcards(pattern)


@pytest.mark.parametrize(
    "pattern, names",
    [
        ("bar_${*foo}", ["foo"]),
        ("bar_*foo", []),
        ("{*bar}_$*foo", []),
        ("${*bar}_${*foo}", ["bar", "foo"]),
    ],
)
def test_iter_wildcard_names(pattern, names):
    assert list(iter_wildcard_names(pattern)) == names


def test_nglob_single_simple(tmpdir):
    pattern = "path/${*prefix}/foo*/${*prefix}-main.txt"
    ngs = NGlobSingle(pattern, {})
    assert ngs.pattern == pattern
    assert ngs.subs == {}
    assert ngs.used_names == ("prefix",)
    added_values = list(
        ngs.extend(
            [
                "path/some/foo1/some-main.txt",
                "path/other/foo1/other-main.txt",
                "path/other/foo2/other-main.txt",
                "path/other/foo1/some-main.txt",
            ]
        )
    )
    assert added_values == [("some",), ("other",)]
    assert ngs.results == {
        ("other",): {
            "path/other/foo1/other-main.txt",
            "path/other/foo2/other-main.txt",
        },
        ("some",): {"path/some/foo1/some-main.txt"},
    }
    deleted_values = list(ngs.reduce(["path/some/foo1/some-main.txt"]))
    assert deleted_values == [("some",)]
    assert ngs.results == {
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
        added_values = list(ngs.glob())
        assert added_values == [("blub",)]
    assert ngs.results == {
        ("other",): {
            "path/other/foo1/other-main.txt",
            "path/other/foo2/other-main.txt",
        },
        ("blub",): {"path/blub/foo1/blub-main.txt", "path/blub/foo2/blub-main.txt"},
    }
    lookup = lookupdict()
    data = msgpack.packb(ngs.unstructure(lookup))
    assert ngs == NGlobSingle.structure(msgpack.unpackb(data), lookup.get_list())


def test_nglob_single_simple_subs():
    pattern = "path/${*prefix}/foo${*num}/${*prefix}-main.txt"
    subs = {"num": "[0-9]"}
    ngs = NGlobSingle(pattern, subs)
    assert ngs.used_names == ("num", "prefix")
    added_values = list(
        ngs.extend(
            [
                "path/some/foo1/some-main.txt",
                "path/other/foo1/other-main.txt",
                "path/other/foo2/other-main.txt",
                "path/other/foo_/other-main.txt",
                "path/other/foo1/some-main.txt",
            ]
        )
    )
    assert added_values == [("1", "some"), ("1", "other"), ("2", "other")]
    assert ngs.results == {
        ("1", "some"): {"path/some/foo1/some-main.txt"},
        ("1", "other"): {"path/other/foo1/other-main.txt"},
        ("2", "other"): {"path/other/foo2/other-main.txt"},
    }
    lookup = lookupdict()
    data = msgpack.packb(ngs.unstructure(lookup))
    assert ngs == NGlobSingle.structure(msgpack.unpackb(data), lookup.get_list())


def test_nglob_single_anonymous():
    paths = [
        "path/some/foo1/some-main.txt",
        "path/other/foo1/other-main.txt",
        "path/other/foo2/other-main.txt",
        "path/other/foo1/some-main.txt",
    ]
    pattern = "path/*/foo*/*-main.txt"
    subs = {}
    ngs = NGlobSingle(pattern, {})
    assert ngs.pattern == pattern
    assert ngs.subs == subs
    assert ngs.used_names == ()
    added_values = list(ngs.extend(paths))
    assert added_values == [()]
    assert ngs.results == {(): set(paths)}
    lookup = lookupdict()
    data = msgpack.packb(ngs.unstructure(lookup))
    assert ngs == NGlobSingle.structure(msgpack.unpackb(data), lookup.get_list())


@pytest.mark.parametrize(
    "patterns, subs",
    [
        (["inp*.txt"], {}),
        (["${*inp}.txt"], {}),
        (["${*inp}.txt"], {"inp": "???"}),
        (["${*inp}.txt", "*.out"], {"inp": "foo"}),
        (["${*inp}.txt", "${*out}.txt"], {"inp": "foo"}),
    ],
)
def test_nglob_multi_has_wildcards_true(patterns, subs):
    ngs = tuple(NGlobSingle(pattern, subs) for pattern in patterns)
    assert NGlobMulti(ngs).has_wildcards


@pytest.mark.parametrize(
    "patterns, subs",
    [
        (["inp.txt"], {}),
        (["${inp}.txt"], {}),
        (["${*inp}.txt"], {"inp": "foo"}),
        (["inp.txt", "${*out}.txt"], {"out": "bar"}),
    ],
)
def test_nglob_multi_has_wildcards_false(patterns, subs):
    ngs = tuple(NGlobSingle(pattern, subs) for pattern in patterns)
    assert not NGlobMulti(ngs).has_wildcards


def test_nglob_multi_iterators_anonymous():
    subs = {"inp": "pre_*", "bar": "??"}
    ngm = NGlobMulti.from_patterns(["pre_*.txt", "*.log"], subs)
    assert ngm.subs is subs
    assert ngm.has_wildcards
    assert len(ngm.used_names) == 0

    # Add a few things and test
    ngm.extend(["pre_fir.txt", "pre_sec.txt", "m.log", "n.log", "z.log"])
    for files in ngm.files(), tuple(ngm):
        assert files == ("m.log", "n.log", "pre_fir.txt", "pre_sec.txt", "z.log")
    matches = list(ngm.matches())
    assert len(matches) == 1
    match = matches[0]
    assert match.mapping == {}
    assert match.files == [["pre_fir.txt", "pre_sec.txt"], ["m.log", "n.log", "z.log"]]
    assert match[0] == ["pre_fir.txt", "pre_sec.txt"]
    assert match[1] == ["m.log", "n.log", "z.log"]
    with pytest.raises(IndexError):
        _ = match[2]
    with pytest.raises(AttributeError):
        _ = match.anything
    lookup = lookupdict()
    data = msgpack.packb(ngm.unstructure(lookup))
    assert ngm == NGlobMulti.structure(msgpack.unpackb(data), lookup.get_list())

    assert ngm.may_change({"m.log"}, set())
    assert ngm.may_change(set(), {"pre_foo.txt"})
    assert not ngm.may_change({"k.log"}, set())
    assert not ngm.may_change(set(), {"pre_fir.txt"})

    ngm.reduce(["pre_sec.txt", "m.log"])
    for files in ngm.files(), tuple(ngm):
        assert files == ("n.log", "pre_fir.txt", "z.log")
    matches = list(ngm.matches())
    assert len(matches) == 1
    match = matches[0]
    assert match.mapping == {}
    assert match.files == [["pre_fir.txt"], ["n.log", "z.log"]]
    assert match[0] == ["pre_fir.txt"]
    assert match[1] == ["n.log", "z.log"]
    assert ngm == ngm.deepcopy()

    assert ngm.will_change({"z.log"}, set())
    assert ngm.will_change(set(), {"pre_foo.txt"})
    assert not ngm.will_change({"k.log"}, set())
    assert not ngm.will_change(set(), {"pre_fir.txt"})


def test_nglob_multi_iterators_named():
    subs = {"inp": "prefix_*", "foo": "??"}
    ngm = NGlobMulti.from_patterns(["${*name}.txt", "*.log", "${*name}_${*suffix}.pdf"], subs)
    assert ngm.subs is subs
    ngm.extend(["fir.txt", "sec.txt", "m.log", "n.log", "fir_a.pdf", "fir_b.pdf", "sec_c.pdf"])
    assert ngm.has_wildcards
    assert ngm.used_names == ("name", "suffix")
    assert ngm.files() == (
        "fir.txt",
        "fir_a.pdf",
        "fir_b.pdf",
        "m.log",
        "n.log",
        "sec.txt",
        "sec_c.pdf",
    )
    for matches in list(ngm.matches()), list(ngm):
        assert matches[0].name == "fir"
        assert matches[0].suffix == "a"
        assert matches[0][0] == "fir.txt"
        assert matches[0][1] == ["m.log", "n.log"]
        assert matches[0][2] == "fir_a.pdf"
        assert matches[0].mapping == {"name": "fir", "suffix": "a"}
        assert matches[0].files == ["fir.txt", ["m.log", "n.log"], "fir_a.pdf"]

        assert matches[1].name == "fir"
        assert matches[1].suffix == "b"
        assert matches[1][0] == "fir.txt"
        assert matches[1][1] == ["m.log", "n.log"]
        assert matches[1][2] == "fir_b.pdf"
        assert matches[1].mapping == {"name": "fir", "suffix": "b"}
        assert matches[1].files == ["fir.txt", ["m.log", "n.log"], "fir_b.pdf"]

        assert matches[2].name == "sec"
        assert matches[2].suffix == "c"
        assert matches[2][0] == "sec.txt"
        assert matches[2][1] == ["m.log", "n.log"]
        assert matches[2][2] == "sec_c.pdf"
        assert matches[2].mapping == {"name": "sec", "suffix": "c"}
        assert matches[2].files == ["sec.txt", ["m.log", "n.log"], "sec_c.pdf"]

        with pytest.raises(IndexError):
            _ = matches[0][3]
        with pytest.raises(AttributeError):
            _ = matches[0].anything

    lookup = lookupdict()
    data = msgpack.packb(ngm.unstructure(lookup))
    assert ngm == NGlobMulti.structure(msgpack.unpackb(data), lookup.get_list())

    assert ngm.may_change({"sec.txt"}, set())
    assert ngm.may_change({"sec_c.pdf"}, set())
    assert ngm.may_change({"m.log"}, set())
    assert ngm.may_change({"fir_b.pdf"}, set())
    assert not ngm.may_change({"sec_d.pdf"}, set())
    assert not ngm.may_change({"k.log"}, set())
    assert ngm.may_change(set(), {"k.log"})
    assert ngm.may_change(set(), {"thi.txt"})
    assert ngm.may_change(set(), {"thi_x.pdf"})
    assert not ngm.may_change(set(), {"k.loog"})

    assert ngm.will_change({"sec.txt"}, set())
    assert ngm.will_change({"sec_c.pdf"}, set())
    assert ngm.will_change({"m.log"}, set())
    assert ngm.will_change({"fir_b.pdf"}, set())
    assert not ngm.will_change({"sec_d.pdf"}, set())
    assert not ngm.will_change({"k.log"}, set())
    assert ngm.will_change(set(), {"k.log"})
    assert not ngm.will_change(set(), {"thi.txt"})
    assert not ngm.will_change(set(), {"thi_x.pdf"})
    assert ngm.will_change(set(), {"thi.txt", "thi_x.pdf"})
    assert not ngm.will_change(set(), {"k.loog"})


@pytest.mark.parametrize(
    "string, matches",
    [
        ("foo*", ["*"]),
        ("foo**", ["**"]),
        ("foo${*bar}", ["${*bar}"]),
        ("*foo${*bar}", ["*", "${*bar}"]),
        ("***foo${*bar}", ["**", "*", "${*bar}"]),
        ("**spam*foo${*bar}", ["**", "*", "${*bar}"]),
        ("*spam**foo${*bar}", ["*", "**", "${*bar}"]),
        ("*${*spam}**foo${*bar}", ["*", "${*spam}", "**", "${*bar}"]),
        ("*foo?", ["*", "?"]),
        ("?foo??", ["?", "?", "?"]),
        ("?foo[ab]?", ["?", "[ab]", "?"]),
        ("?foo[a-z][0-9][^?][?]?", ["?", "[a-z]", "[0-9]", "[^?]", "[?]", "?"]),
        ("foo[?]", ["[?]"]),
        ("foo[*]", ["[*]"]),
        ("foo[${*ab}]", ["[${*ab}]"]),
        ("foo[[]a]", ["[[]"]),
    ],
)
def test_nglob_wild(string, matches):
    assert re.findall(RE_NAMED_WILD, string) == matches


@pytest.mark.parametrize(
    "pattern, normal",
    [
        ("generic/${*ch}/*.md", "generic/*/*.md"),
        ("generic/*${*ch}/*.md", "generic/*/*.md"),
        ("generic/${*ch}*/*.md", "generic/*/*.md"),
        ("generic/*${*ch}*/*.md", "generic/*/*.md"),
        ("generic/*${*ch}**/*.md", "generic/**/*.md"),
        ("generic/**${*ch}*/*.md", "generic/**/*.md"),
        ("generic/**${*ch}**/*.md", "generic/**/*.md"),
        ("generic/${*ch}${*foo}/*.md", "generic/*/*.md"),
        ("generic/${*ch}-${*foo}/*.md", "generic/*-*/*.md"),
        ("generic/${*ch}/${*foo}/*.md", "generic/*/*/*.md"),
        ("${*generic}/ch${*foo}/*.md", "*/ch*/*.md"),
        ("generic/ch${*foo}/${*md}", "generic/ch*/*"),
        ("generic/${*md}${*ch}/${*md}", "generic/*/*"),
        ("generic/${*md}?/${*md}", "generic/*/*"),
        ("generic/**?/?${*md}", "generic/**/*"),
        ("generic/?**/*?", "generic/**/*"),
        ("generic/${*md}[a[b]/?[*]", "generic/*[a[b]/?[*]"),
    ],
)
def test_nglob_to_glob(pattern, normal):
    assert convert_nglob_to_glob(pattern) == normal


@pytest.mark.parametrize(
    "pattern, subs, normal",
    [
        (
            "${*generic}/${*ch}/*.md",
            {"generic": "?[ab]*", "ch": "s_*_*"},
            "?[ab]*/s_*_*/*.md",
        ),
        ("${*a}${*b}/ab", {"a": "a*"}, "a*/ab"),
        ("${*a}${*b}/ab", {"a": "a*", "b": "?b"}, "a*b/ab"),
        ("${*a}${*b}${*a}/ab", {"a": "?a*", "b": "**b*"}, "?a**b*a*/ab"),
    ],
)
def test_nglob_to_glob_subs(pattern, subs, normal):
    assert convert_nglob_to_glob(pattern, subs) == normal


@pytest.mark.parametrize(
    "pattern, regex",
    [
        ("generic/${*ch}/*.md", r"generic/(?P<ch>[^/]*)/[^/]*\.md"),
        ("generic/${*ch}/?.md", r"generic/(?P<ch>[^/]*)/[^/]\.md"),
        ("generic/${*ch}/[abc].md", r"generic/(?P<ch>[^/]*)/[abc]\.md"),
        ("generic/${*ch}/[!abc].md", r"generic/(?P<ch>[^/]*)/[^abc]\.md"),
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
            r"generic/(?P<ch>[^/]*)/(?P<foo>[^/]*)/[^/]*\.md",
        ),
        (
            "generic/${*ch}**${*foo}/*.md",
            r"generic/(?P<ch>[^/]*).*(?P<foo>[^/]*)/[^/]*\.md",
        ),
        (
            "generic/${*ch}**/${*foo}/*.md",
            r"generic/(?P<ch>[^/]*).*/(?P<foo>[^/]*)/[^/]*\.md",
        ),
        (
            "${*generic}/ch${*foo}/*.md",
            r"(?P<generic>[^/]*)/ch(?P<foo>[^/]*)/[^/]*\.md",
        ),
        ("generic/ch${*foo}/${*md}", r"generic/ch(?P<foo>[^/]*)/(?P<md>[^/]*)"),
        ("generic/${*md}${*ch}/${*md}", "generic/(?P<md>[^/]*)(?P<ch>[^/]*)/(?P=md)"),
    ],
)
def test_nglob_to_regex(pattern, regex):
    assert convert_nglob_to_regex(pattern) == regex


@pytest.mark.parametrize(
    "pattern, subs, regex",
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
    regex = re.compile(convert_nglob_to_regex("generic/${*ch}**/${*foo}/*.md"))
    match_ = regex.fullmatch("generic/ch1/some/some/name/file.md")
    assert match_.groups() == ("ch1", "name")


def _make_files(paths: Collection[str]):
    for path in paths:
        path = Path(path)
        if len(path.parent) > 0:
            path.parent.makedirs_p()
        with open(path, "w"):
            pass


def _check_ngm_multi(tmpdir, patterns, subs, paths, used_names, results):
    ngm1 = NGlobMulti.from_patterns(patterns, subs)
    assert ngm1.used_names == used_names
    assert ngm1.subs == subs
    assert ngm1.has_wildcards
    ngm1.extend(paths)
    assert ngm1.results == results
    assert bool(ngm1) == (len(results) > 0)
    with contextlib.chdir(tmpdir):
        _make_files(paths)
        ngm2 = NGlobMulti.from_patterns(patterns, subs)
        ngm2.glob()
        assert ngm2.results == results
    return ngm1


def test_nglob_multi_single_nowildcards():
    ngm = NGlobMulti.from_patterns(["inp1.txt"])
    ngm.extend(["inp1.txt", "foo.bar"])
    assert ngm.results == {(): [{"inp1.txt"}]}
    items = list(ngm)
    assert len(items) == 1
    assert isinstance(items[0], str)


def test_nglob_multi_single_nonames():
    ngm = NGlobMulti.from_patterns(["*.log"])
    ngm.extend(["inp.txt", "foo.bar", "worker.log", "director.log"])
    assert ngm.results == {(): [{"director.log", "worker.log"}]}
    assert list(ngm) == ["director.log", "worker.log"]


def test_nglob_multi_nonames():
    ngm = NGlobMulti.from_patterns(["*.txt", "*.log"])
    ngm.extend(["foo.bar", "worker.log", "director.log", "inp.txt"])
    assert ngm.results == {(): [{"inp.txt"}, {"director.log", "worker.log"}]}
    assert list(ngm) == ["director.log", "inp.txt", "worker.log"]


def test_nglob_multi_single_names():
    ngm = NGlobMulti.from_patterns(["prefix_${*f}_${*f}.txt"])
    ngm.extend(["prefix_a_b.txt", "prefix_b_a.txt", "prefix_b_b.txt", "prefix_a_a.txt"])
    assert ngm.results == {("a",): [{"prefix_a_a.txt"}], ("b",): [{"prefix_b_b.txt"}]}
    assert list(ngm) == [
        NGlobMatch({"f": "a"}, [Path("prefix_a_a.txt")]),
        NGlobMatch({"f": "b"}, [Path("prefix_b_b.txt")]),
    ]


def test_nglob_multi_named1(tmpdir):
    patterns = ["${*dir}/foo.txt", "${*dir}/b?r${*id}.csv"]
    paths = [
        "b/foo.txt",
        "a/foo.txt",
        "b/bar3.csv",
        "b/bir4.csv",
        "b/bar5.csv",
        "a/bar1.csv",
        "a/bir1.csv",
        "a/bar2.csv",
    ]
    used_names = ("dir", "id")
    results = {
        ("a", "1"): [{"a/foo.txt"}, {"a/bar1.csv", "a/bir1.csv"}],
        ("a", "2"): [{"a/foo.txt"}, {"a/bar2.csv"}],
        ("b", "3"): [{"b/foo.txt"}, {"b/bar3.csv"}],
        ("b", "4"): [{"b/foo.txt"}, {"b/bir4.csv"}],
        ("b", "5"): [{"b/foo.txt"}, {"b/bar5.csv"}],
    }
    ngm = _check_ngm_multi(tmpdir, patterns, {}, paths, used_names, results)
    assert all(isinstance(item, NGlobMatch) for item in ngm)


def test_nglob_multi_named1_subs(tmpdir):
    patterns = ["${*dir}/foo.txt", "${*dir}/b?r${*id}.csv"]
    subs = {"id": "[125]"}
    paths = [
        "b/foo.txt",
        "a/foo.txt",
        "b/bar3.csv",
        "b/bir4.csv",
        "b/bar5.csv",
        "a/bar1.csv",
        "a/bir1.csv",
        "a/bar2.csv",
    ]
    used_names = ("dir", "id")
    results = {
        ("a", "1"): [{"a/foo.txt"}, {"a/bar1.csv", "a/bir1.csv"}],
        ("a", "2"): [{"a/foo.txt"}, {"a/bar2.csv"}],
        ("b", "5"): [{"b/foo.txt"}, {"b/bar5.csv"}],
    }
    ngm = _check_ngm_multi(tmpdir, patterns, subs, paths, used_names, results)
    assert all(isinstance(item, NGlobMatch) for item in ngm)


def test_filter_named2(tmpdir):
    patterns = ["${*dir}/foo.txt", "other/${*name}.txt"]
    paths = ["b/foo.txt", "a/foo.txt", "other/spam.txt", "other/egg.txt"]
    used_names = ("dir", "name")
    results = {
        ("a", "egg"): [{"a/foo.txt"}, {"other/egg.txt"}],
        ("a", "spam"): [{"a/foo.txt"}, {"other/spam.txt"}],
        ("b", "egg"): [{"b/foo.txt"}, {"other/egg.txt"}],
        ("b", "spam"): [{"b/foo.txt"}, {"other/spam.txt"}],
    }
    _check_ngm_multi(tmpdir, patterns, {}, paths, used_names, results)


def test_filter_named2_subs(tmpdir):
    patterns = ["${*dir}/foo.txt", "other/${*name}.txt"]
    paths = ["b/foo.txt", "a/foo.txt", "other/spam.txt", "other/egg.txt"]
    subs = {"name": "???"}
    used_names = ("dir", "name")
    results = {
        ("a", "egg"): [{"a/foo.txt"}, {"other/egg.txt"}],
        ("b", "egg"): [{"b/foo.txt"}, {"other/egg.txt"}],
    }
    _check_ngm_multi(tmpdir, patterns, subs, paths, used_names, results)


def test_may_match():
    patterns = ["subdir*/", "foo*.txt"]
    ngm = NGlobMulti.from_patterns(patterns)
    assert not ngm.may_match("subdir/foo.txt")
    assert not ngm.may_match("foo.log")
    assert ngm.may_match("subdir1/")
    assert ngm.may_match("foo1.txt")


def test_may_will_change_fullmatch():
    patterns = ["subdir*/", "foo*.txt"]
    ngm = NGlobMulti.from_patterns(patterns)
    assert not ngm.may_change(set(), {"subdir/foo.txt"})
    assert not ngm.may_change(set(), {"foo.log"})
    assert ngm.may_change(set(), {"subdir1/"})
    assert ngm.may_change(set(), {"foo1.txt"})
    assert not ngm.will_change(set(), {"subdir/foo.txt"})
    assert not ngm.will_change(set(), {"foo.log"})
    assert not ngm.will_change(set(), {"subdir1/"})
    assert not ngm.will_change(set(), {"foo2.txt"})
    assert ngm.will_change(set(), {"subdir1/", "foo2.txt"})
