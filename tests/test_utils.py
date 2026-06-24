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
    make_path_out,
    myabsolute,
    mynormpath,
    myparent,
    myrealpath,
    myrelpath,
    parse_resources,
    translate,
    translate_back,
)


@pytest.mark.parametrize(
    ("inp", "out"),
    [
        ("./foo", "foo"),
        ("./dir/", "dir/"),
        (".", "."),
        ("./", "./"),
        ("/foo/", "/foo/"),
        ("/foo", "/foo"),
        ("/foo/../bar", "/bar"),
        ("/foo/./bar", "/foo/bar"),
        ("./foo/./bar/", "foo/bar/"),
        ("/", "/"),
    ],
)
def test_mynormpath(inp: str, out: str):
    assert mynormpath(inp) == out


@pytest.mark.parametrize(
    ("inp", "start", "out"),
    [
        ("/foo/bar/", "/foo", "bar/"),
        ("/foo/bar", "/foo", "bar"),
        ("/foo/sub/../extra/some", "/foo/bar", "../extra/some"),
        ("/foo/sub/../extra/some/", "/foo/bar", "../extra/some/"),
    ],
)
def test_myrelpath(inp: str, start: str, out: str):
    assert myrelpath(inp, start) == out


@pytest.mark.parametrize(
    "inp",
    [
        "/foo/bar/",
        "/foo/bar",
        "/foo/sub/../extra/some",
        "/foo/sub/../extra/some/",
        "/",
    ],
)
def test_myabsolute(inp: str):
    assert myabsolute(inp, is_dir=True).endswith("/")
    if inp.endswith("/"):
        assert myabsolute(inp).endswith("/")
    assert myabsolute(inp).normpath() == Path(inp).absolute()


def test_myrealpath(tmp_path: Path):
    (tmp_path / "dir").mkdir()
    (tmp_path / "dir/file.txt").write_text("hello")
    (tmp_path / "link").symlink_to(tmp_path / "dir/file.txt")
    (tmp_path / "dir/linkdir").symlink_to(tmp_path / "dir")
    assert myrealpath(str(tmp_path / "link")) == str(tmp_path / "dir/file.txt")
    assert myrealpath(str(tmp_path / "dir/linkdir/file.txt")) == str(tmp_path / "dir/file.txt")
    assert myrealpath(str(tmp_path / "dir/linkdir/")) == str(tmp_path / "dir/")


@pytest.mark.parametrize(
    ("inp", "out"),
    [
        ("foo/bar", "foo/"),
        ("foo/bar/", "foo/"),
        ("foo/bar/egg.txt", "foo/bar/"),
        ("foo/", "./"),
        ("foo", "./"),
        (".", None),
        ("./", None),
        ("/", None),
        ("/usr", "/"),
    ],
)
def test_myparent(inp, out):
    assert myparent(inp) == out


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
