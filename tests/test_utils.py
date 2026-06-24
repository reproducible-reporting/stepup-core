# StepUp Core provides the basic framework for the StepUp build tool.
# Copyright 2024-2026 Toon Verstraelen
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
"""Tests for stepup.core.utils."""

import pytest
from path import Path

from stepup.core.utils import (
    apply_affixes,
    get_affixes,
    make_path_out,
    parse_resources,
    translate,
    translate_back,
)


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("foo", ("", "")),
        ("sub/foo", ("", "")),
        ("./foo", ("./", "")),
        ("foo/", ("", "/")),
        ("./foo/", ("./", "/")),
        ("./sub/foo/", ("./", "/")),
        ("/foo", ("", "")),
        ("/foo/", ("", "/")),
        (".", ("", "")),
        ("..", ("", "")),
        ("./", ("", "/")),
    ],
)
def test_get_affixes(path, expected):
    assert get_affixes(path) == expected


@pytest.mark.parametrize(
    ("path", "leading", "trailing", "expected"),
    [
        ("foo", "", "", "foo"),
        ("sub/foo", "", "", "sub/foo"),
        ("foo", "./", "", "./foo"),
        ("foo", "", "/", "foo/"),
        ("foo", "./", "/", "./foo/"),
        ("sub/foo", "./", "/", "./sub/foo/"),
    ],
)
def test_apply_affixes(path, leading, trailing, expected):
    assert apply_affixes(path, leading, trailing) == expected


@pytest.mark.parametrize(
    ("path", "leading", "trailing"),
    [
        ("foo", "../", ""),  # leading must be "" or "./"
        ("foo", ".", ""),  # leading must be "" or "./"
        ("foo", "", "//"),  # trailing must be "" or "/"
        ("foo", "", "."),  # trailing must be "" or "/"
        ("./foo", "./", ""),  # path already has a leading "./"
        ("/foo", "./", ""),  # path already has a leading "/"
        ("foo/", "", "/"),  # path already has a trailing "/"
    ],
)
def test_apply_affixes_invalid(path, leading, trailing):
    with pytest.raises(ValueError):
        apply_affixes(path, leading, trailing)


@pytest.mark.parametrize(
    "path",
    ["foo", "sub/foo", "./foo", "foo/", "./foo/", "./sub/foo/", "/foo", "/foo/", "./"],
)
def test_affixes_round_trip(path):
    # Extracting the affixes and re-applying them to the stripped path restores the original.
    leading, trailing = get_affixes(path)
    stripped = path[len(leading) : len(path) - len(trailing)]
    assert apply_affixes(stripped, leading, trailing) == path


def test_make_path_out():
    assert make_path_out("foo.svg", None, ".pdf") == "foo.pdf"
    assert make_path_out("foo.svg", "dst/", ".pdf") == "dst/foo.pdf"
    assert make_path_out("foo.svg", "dst/", None) == "dst/foo.svg"
    assert make_path_out("foo.svg", "bar.pdf", ".pdf") == "bar.pdf"
    assert make_path_out("sub/foo.svg", None, ".pdf") == "sub/foo.pdf"
    assert make_path_out("sub/foo.svg", "dst/", ".pdf") == "dst/foo.pdf"
    assert make_path_out("sub/foo.svg", "dst/", None) == "dst/foo.svg"
    assert make_path_out("sub/foo.svg", "bar.pdf", ".pdf") == "bar.pdf"
    with pytest.raises(ValueError):
        make_path_out("foo.svg", "bar.txt", ".pdf")
    with pytest.raises(ValueError):
        make_path_out("foo.pdf", "foo.pdf", ".pdf")
    with pytest.raises(ValueError):
        make_path_out("foo.pdf", None, ".pdf")
    with pytest.raises(ValueError):
        make_path_out("foo.pdf", None, None)


def test_make_path_out_other_exts():
    assert make_path_out("foo.svg", None, ".pdf", [".png"]) == "foo.pdf"
    assert make_path_out("foo.svg", "bar.png", ".pdf", [".png"]) == "bar.png"
    with pytest.raises(ValueError):
        make_path_out("foo.svg", "bar.jpg", ".pdf", [".png"])


@pytest.mark.parametrize("with_stepup_root", [True, False])
def test_translate(monkeypatch, with_stepup_root):
    if with_stepup_root:
        monkeypatch.setenv("STEPUP_ROOT", Path.cwd())
    monkeypatch.setenv("HERE", "foo/bar")
    assert translate("somefile.txt") == "foo/bar/somefile.txt"
    assert translate("somefile.txt", "../") == "foo/somefile.txt"
    assert translate("somefile.txt", "/egg/") == "/egg/somefile.txt"
    assert translate("/somefile.txt", "/egg/") == "/somefile.txt"
    assert translate("/egg/somefile.txt", "../") == "/egg/somefile.txt"
    assert translate("/somefile.txt", "../") == "/somefile.txt"


def test_translate_outside(monkeypatch):
    monkeypatch.setenv("STEPUP_ROOT", Path.cwd() / "foo")
    monkeypatch.setenv("HERE", "bar")
    assert translate("somefile.txt") == "bar/somefile.txt"
    assert translate("somefile.txt", "../") == "somefile.txt"
    assert translate("../../foo/somefile.txt") == "somefile.txt"
    assert translate("somefile.txt", "/egg/") == "/egg/somefile.txt"
    assert translate("/somefile.txt", "/egg/") == "/somefile.txt"
    assert translate("/egg/somefile.txt", "../") == "/egg/somefile.txt"
    assert translate("/somefile.txt", "../") == "/somefile.txt"


@pytest.mark.parametrize("with_stepup_root", [True, False])
def test_translate_back(monkeypatch, with_stepup_root):
    if with_stepup_root:
        monkeypatch.setenv("STEPUP_ROOT", Path.cwd())
    monkeypatch.setenv("HERE", "foo/bar")
    assert translate_back("foo/bar/somefile.txt") == "somefile.txt"
    assert translate_back("foo/somefile.txt", "../") == "somefile.txt"
    assert translate_back("/egg/somefile.txt", "/egg/") == "somefile.txt"
    assert translate_back("/somefile.txt", "/egg/") == "/somefile.txt"
    assert translate_back("/egg/somefile.txt", "../") == "/egg/somefile.txt"
    assert translate_back("/somefile.txt", "../") == "/somefile.txt"


def test_translate_back_outside(monkeypatch):
    monkeypatch.setenv("STEPUP_ROOT", "/home/dude/project/source/")
    monkeypatch.setenv("HERE", "../../bar/egg")
    assert translate_back("../public") == "../../project/public"


@pytest.mark.parametrize(
    ("s", "expected"),
    [
        ("cpu:4,gpu:1,memgb:16", {"cpu": 4, "gpu": 1, "memgb": 16}),
        ("cpu:2", {"cpu": 2}),
        ("cpu:0", {"cpu": 0}),
        ("cpu:", {"cpu": 1}),
        ("cpu", {"cpu": 1}),
        ("  cpu : 4 , gpu ", {"cpu": 4, "gpu": 1}),
        ("", {}),
        (",", {}),
        (",,,", {}),
    ],
)
def test_parse_resources(s, expected):
    assert parse_resources(s) == expected


@pytest.mark.parametrize(
    "s",
    [
        "cpu:-1",
        ":1",
        "  :2",
    ],
)
def test_parse_resources_invalid(s):
    with pytest.raises(ValueError):
        parse_resources(s)
