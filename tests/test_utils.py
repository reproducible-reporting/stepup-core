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
"""Tests for stepup.core.utils."""

import pytest
from path import Path

from stepup.core.utils import (
    load_module_file,
    make_path_out,
    myabsolute,
    mynormpath,
    myparent,
    myrelpath,
)


@pytest.mark.parametrize(
    "inp, out",
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
    "inp, start, out",
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
    "inp, out",
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


def test_load_module_file(path_tmp):
    path_foo = path_tmp / "foo.py"
    with open(path_foo, "w") as fh:
        print("a = 10", file=fh)
    foo = load_module_file(path_foo)
    assert foo.a == 10
