# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Tests for stepup.core.path."""

import pathlib

import pytest
from path import Path

from stepup.core.path import (
    apply_affixes,
    coerce_path,
    coerce_paths,
    coerce_paths2,
    coerce_str,
    dir_range_upper,
    get_affixes,
    get_here,
    get_root,
    make_path_out,
    translate,
    translate_back,
)


def test_coerce_path():
    for arg in "sub/asdf", Path("sub/asdf"), pathlib.PurePath("sub/asdf"):
        result = coerce_path(arg)
        assert isinstance(result, Path)
        assert result == Path("sub/asdf")


def test_coerce_path_affixes():
    # A trailing slash in a str is preserved, but pathlib strips it at construction.
    assert coerce_path("dir/") == "dir/"
    assert coerce_path(pathlib.PurePath("dir/")) == "dir"


def test_coerce_paths():
    # A single path-like argument.
    for arg in "sub/asdf", Path("sub/asdf"), pathlib.PurePath("sub/asdf"):
        result = coerce_paths(arg)
        assert result == [Path("sub/asdf")]
        assert all(isinstance(item, Path) for item in result)
    # A mixed collection of path-like arguments.
    result = coerce_paths(["a/b", Path("c/d"), pathlib.PurePath("e/f")])
    assert result == [Path("a/b"), Path("c/d"), Path("e/f")]
    assert all(isinstance(item, Path) for item in result)
    # An empty collection.
    assert coerce_paths(()) == []


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (
            [
                "a/b",
                [Path("c/d"), pathlib.PurePath("e/f")],
                ("g/h",),
                [],
            ],
            [Path("a/b"), Path("c/d"), Path("e/f"), Path("g/h")],
        ),
        ([], []),
    ],
)
def test_coerce_paths2(args, expected):
    result = coerce_paths2(args)
    assert result == expected
    assert all(isinstance(item, Path) for item in result)


@pytest.mark.parametrize(
    ("arg", "expected"),
    [
        ("sub/asdf", "sub/asdf"),
        (Path("sub/asdf"), "sub/asdf"),
        (pathlib.PurePath("sub/asdf"), "sub/asdf"),
    ],
)
def test_coerce_str(arg, expected):
    # os.fspath semantics: str is returned unchanged; os.PathLike is converted to str.
    assert coerce_str(arg) == expected


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


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("out/", "out0"),
        ("out/report/", "out/report0"),
        ("é/", "é0"),
    ],
)
def test_dir_range_upper(path, expected):
    # The exclusive upper bound must sort just past every label under the directory
    # (byte-wise, matching SQLite's default BINARY collation on node.label), while a
    # boundary sibling sharing the prefix without the slash falls outside the range.
    upper = dir_range_upper(path)
    assert upper == expected
    assert path < upper  # the directory node itself is in range
    assert path <= f"{path}sub/file.txt" < upper
    assert not (path <= path[:-1] + "_sibling" < upper)


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
def test_translate(monkeypatch, path_tmp, with_stepup_root):
    if with_stepup_root:
        monkeypatch.setenv("STEPUP_ROOT", path_tmp)
        workdir = path_tmp / "foo/bar"
        workdir.makedirs()
        prefix = "foo/bar/"
    else:
        monkeypatch.delenv("STEPUP_ROOT", raising=False)
        workdir = path_tmp
        prefix = ""
    monkeypatch.chdir(workdir)
    assert translate("somefile.txt") == f"{prefix}somefile.txt"
    assert translate("somefile.txt", "../") == Path(f"{prefix}../somefile.txt").normpath()
    assert translate("somefile.txt", "/egg/") == "/egg/somefile.txt"
    assert translate("/somefile.txt", "/egg/") == "/somefile.txt"
    assert translate("/egg/somefile.txt", "../") == "/egg/somefile.txt"
    assert translate("/somefile.txt", "../") == "/somefile.txt"


def test_translate_ignores_here(monkeypatch, path_tmp):
    monkeypatch.setenv("STEPUP_ROOT", path_tmp)
    (path_tmp / "foo/bar").makedirs()
    monkeypatch.chdir(path_tmp / "foo")
    monkeypatch.setenv("HERE", "foo/bar")
    assert translate("somefile.txt") == "foo/somefile.txt"
    assert translate_back("foo/somefile.txt") == "somefile.txt"


def test_get_here_root(monkeypatch, path_tmp):
    (path_tmp / "project/sub/deep").makedirs()
    (path_tmp / "outside").makedirs()
    monkeypatch.setenv("STEPUP_ROOT", path_tmp / "project")
    monkeypatch.chdir(path_tmp / "project/sub")
    # Out-of-date environment variables are ignored.
    monkeypatch.setenv("HERE", "wrong")
    monkeypatch.setenv("ROOT", "wrong")
    assert get_here() == "sub"
    assert get_root() == ".."
    assert get_here("deep") == "sub/deep"
    assert get_root("deep/") == "../.."
    assert get_here("..") == "."
    assert get_root("..") == "."
    assert get_here(path_tmp / "outside") == "../outside"
    assert get_root(path_tmp / "outside") == "../project"
    monkeypatch.chdir(path_tmp / "outside")
    assert get_here() == "../outside"
    assert get_root() == "../project"


def test_get_here_root_no_stepup_root(monkeypatch, path_tmp):
    (path_tmp / "sub").makedirs()
    monkeypatch.delenv("STEPUP_ROOT", raising=False)
    monkeypatch.chdir(path_tmp)
    assert get_here() == "."
    assert get_root() == "."
    assert get_here("sub") == "sub"
    assert get_root("sub") == ".."


def test_get_here_root_symlinks(monkeypatch, path_tmp):
    (path_tmp / "real/project/sub").makedirs()
    (path_tmp / "link").symlink_to(path_tmp / "real")
    (path_tmp / "real/project/alias").symlink_to(path_tmp / "real/project/sub")
    monkeypatch.setenv("STEPUP_ROOT", path_tmp / "link/project")
    monkeypatch.chdir(path_tmp / "link/project")
    # A symbolic link above the root is harmless.
    assert get_here("sub") == "sub"
    assert get_root("sub") == ".."
    # A symbolic link inside the project is resolved to the physical directory.
    assert get_here("alias") == "sub"
    monkeypatch.chdir(path_tmp / "link/project/alias")
    assert get_here() == "sub"
    assert get_root() == ".."


def test_translate_outside(monkeypatch, path_tmp):
    monkeypatch.setenv("STEPUP_ROOT", path_tmp / "foo")
    (path_tmp / "foo").makedirs()
    (path_tmp / "bar").makedirs()
    monkeypatch.chdir(path_tmp / "bar")
    assert translate("somefile.txt") == "../bar/somefile.txt"
    assert translate("somefile.txt", "../") == "../somefile.txt"
    assert translate("../foo/somefile.txt") == "somefile.txt"
    assert translate("somefile.txt", "/egg/") == "/egg/somefile.txt"
    assert translate("/somefile.txt", "/egg/") == "/somefile.txt"
    assert translate("/egg/somefile.txt", "../") == "/egg/somefile.txt"
    assert translate("/somefile.txt", "../") == "/somefile.txt"


def test_translate_symlink_in_root(monkeypatch, path_tmp):
    (path_tmp / "real/project/sub").makedirs()
    (path_tmp / "link").symlink_to(path_tmp / "real")
    monkeypatch.setenv("STEPUP_ROOT", path_tmp / "link/project")
    monkeypatch.chdir(path_tmp / "link/project/sub")
    assert translate("somefile.txt") == "sub/somefile.txt"
    assert translate_back("sub/somefile.txt") == "somefile.txt"
    monkeypatch.chdir(path_tmp / "real/project")
    assert translate("somefile.txt") == "somefile.txt"
    assert translate_back("sub/somefile.txt") == "sub/somefile.txt"


@pytest.mark.parametrize("with_stepup_root", [True, False])
def test_translate_back(monkeypatch, path_tmp, with_stepup_root):
    if with_stepup_root:
        monkeypatch.setenv("STEPUP_ROOT", path_tmp)
        workdir = path_tmp / "foo/bar"
        workdir.makedirs()
        prefix = "foo/bar/"
    else:
        monkeypatch.delenv("STEPUP_ROOT", raising=False)
        workdir = path_tmp
        prefix = ""
    monkeypatch.chdir(workdir)
    assert translate_back(f"{prefix}somefile.txt") == "somefile.txt"
    assert translate_back(Path(f"{prefix}../somefile.txt").normpath(), "../") == "somefile.txt"
    assert translate_back("/egg/somefile.txt", "/egg/") == "somefile.txt"
    assert translate_back("/somefile.txt", "/egg/") == "/somefile.txt"
    assert translate_back("/egg/somefile.txt", "../") == "/egg/somefile.txt"
    assert translate_back("/somefile.txt", "../") == "/somefile.txt"


def test_translate_back_outside(monkeypatch, path_tmp):
    (path_tmp / "project/source").makedirs()
    (path_tmp / "bar/egg").makedirs()
    monkeypatch.setenv("STEPUP_ROOT", path_tmp / "project/source/")
    monkeypatch.chdir(path_tmp / "bar/egg")
    assert translate_back("../public") == "../../project/public"
