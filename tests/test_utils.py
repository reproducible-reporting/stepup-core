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
"""Tests for stepup.core.utils."""

import contextlib

import pytest
from path import Path

from stepup.core.utils import (
    Translate,
    filter_dependencies,
    make_path_out,
    myabsolute,
    mynormpath,
    myparent,
    myrelpath,
    translate,
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


def test_mak_path_out_other_exts():
    assert make_path_out("foo.svg", None, ".pdf", [".png"]) == "foo.pdf"
    assert make_path_out("foo.svg", "bar.png", ".pdf", [".png"]) == "bar.png"
    with pytest.raises(ValueError):
        make_path_out("foo.svg", "bar.jpg", ".pdf", [".png"])


@pytest.mark.parametrize("with_stepup_root", [True, False])
def test_translate(monkeypatch, with_stepup_root):
    translate = Translate(Path.cwd(), "foo/bar") if with_stepup_root else Translate(here="foo/bar")
    assert translate("somefile.txt") == "foo/bar/somefile.txt"
    assert translate("somefile.txt", "../") == "foo/somefile.txt"
    assert translate("somefile.txt", "/egg/") == "/egg/somefile.txt"
    assert translate("/somefile.txt", "/egg/") == "/somefile.txt"
    assert translate("/egg/somefile.txt", "../") == "/egg/somefile.txt"
    assert translate("/somefile.txt", "../") == "/somefile.txt"


def test_translate_outside(monkeypatch):
    translate = Translate(Path.cwd() / "foo", "bar")
    assert translate("somefile.txt") == "bar/somefile.txt"
    assert translate("somefile.txt", "../") == "somefile.txt"
    assert translate("../../foo/somefile.txt") == "somefile.txt"
    assert translate("somefile.txt", "/egg/") == "/egg/somefile.txt"
    assert translate("/somefile.txt", "/egg/") == "/somefile.txt"
    assert translate("/egg/somefile.txt", "../") == "/egg/somefile.txt"
    assert translate("/somefile.txt", "../") == "/somefile.txt"


@pytest.mark.parametrize("with_stepup_root", [True, False])
def test_translate_back(with_stepup_root):
    translate = Translate(Path.cwd(), "foo/bar") if with_stepup_root else Translate(here="foo/bar")
    assert translate.back("foo/bar/somefile.txt") == "somefile.txt"
    assert translate.back("foo/somefile.txt", "../") == "somefile.txt"
    assert translate.back("/egg/somefile.txt", "/egg/") == "somefile.txt"
    assert translate.back("/somefile.txt", "/egg/") == "/somefile.txt"
    assert translate.back("/egg/somefile.txt", "../") == "/egg/somefile.txt"
    assert translate.back("/somefile.txt", "../") == "/somefile.txt"


def test_translate_back_outside():
    translate = Translate("/home/dude/project/source/", "../../bar/egg")
    assert translate.back("../public") == "../../project/public"


def test_filter_dependencies(monkeypatch, path_tmp):
    (path_tmp / "project/sub").makedirs()
    monkeypatch.setenv("STEPUP_ROOT", path_tmp / "project")
    abs_paths = [path_tmp / "project/foo", path_tmp / "project/sub/down/bar"]
    monkeypatch.chdir(path_tmp / "project/sub")
    assert filter_dependencies(abs_paths) == {"../foo", "down/bar"}


def test_filter_dependencies_external(monkeypatch, path_tmp):
    (path_tmp / "project").makedirs()
    (path_tmp / "pkgs").makedirs()
    monkeypatch.setenv("STEPUP_ROOT", path_tmp / "project")
    monkeypatch.setenv("STEPUP_EXTERNAL_SOURCES", path_tmp / "pkgs")
    abs_paths = [path_tmp / "pkgs/helper.py", path_tmp / "other.py"]
    monkeypatch.chdir(path_tmp / "project")
    assert filter_dependencies(abs_paths) == {"../pkgs/helper.py"}


def test_filter_dependencies_external2(monkeypatch, path_tmp):
    (path_tmp / "project").makedirs()
    (path_tmp / "pkgs").makedirs()
    (path_tmp / "templates").makedirs()
    monkeypatch.setenv("STEPUP_ROOT", path_tmp / "project")
    ext_sources = [path_tmp / "pkgs", path_tmp / "templates"]
    monkeypatch.setenv("STEPUP_EXTERNAL_SOURCES", ":".join(ext_sources))
    abs_paths = [
        path_tmp / "pkgs/helper.py",
        path_tmp / "other.py",
        path_tmp / "templates/fancy.typ",
    ]
    monkeypatch.chdir(path_tmp / "project")
    assert filter_dependencies(abs_paths) == {"../pkgs/helper.py", "../templates/fancy.typ"}


def test_filter_dependencies_relative(monkeypatch, path_tmp):
    monkeypatch.setenv("STEPUP_ROOT", path_tmp / "project")
    monkeypatch.setenv("STEPUP_EXTERNAL_SOURCES", "../external")
    (path_tmp / "project/sub").makedirs()
    with contextlib.chdir(path_tmp / "project/sub"):
        monkeypatch.setattr(translate, "root", path_tmp / "project/")
        monkeypatch.setattr(translate, "here", "sub/")
        rel_paths = ["foo", "../../external/bar", "../../egg", "../../other/spam"]
        assert filter_dependencies(rel_paths) == {"foo", "../../external/bar"}


def test_filter_dependencies_arg():
    assert filter_dependencies(["../foo/source.txt", "../bar/library.txt"], "../foo") == {
        "../foo/source.txt"
    }
