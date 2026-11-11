# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Tests for stepup.core.path."""

import ntpath
import os
import pathlib
import pickle
import sys

import path as path_lib
import pytest

from stepup.core.path import (
    Path,
    _NtPath,
    apply_affixes,
    coerce_path,
    coerce_paths,
    coerce_paths2,
    coerce_str,
    dir_range_upper,
    get_affixes,
    get_here,
    get_root,
    is_path_type,
    make_path_out,
    to_slash,
    translate,
    translate_back,
)


def test_path_class():
    path = Path("sub/dir") / "file.txt"
    assert path == "sub/dir/file.txt"
    assert type(path) is Path
    assert isinstance(path, path_lib.Path)
    assert isinstance(path, str)
    assert repr(path) == "Path('sub/dir/file.txt')"
    assert {type(p) for p in (path.parent, path.normpath(), path.with_suffix(".md"))} == {Path}
    assert Path("./sub/") == "./sub/"


def test_path_pickle():
    path = Path("./sub/dir/")
    back = pickle.loads(pickle.dumps(path))
    assert back == path
    assert type(back) is Path


def test_is_path_type():
    assert is_path_type(Path)
    assert is_path_type(path_lib.Path)
    assert is_path_type(_NtPath)
    assert not is_path_type(str)
    assert not is_path_type(pathlib.Path)
    assert not is_path_type(Path("foo"))


def test_to_slash():
    assert to_slash("sub/dir/") == "sub/dir/"
    if sys.platform == "win32":
        assert to_slash("c:\\Users\\me\\") == "C:/Users/me/"
    else:
        assert to_slash("sub\\name") == "sub\\name"


@pytest.mark.parametrize(
    ("arg", "expected"),
    [
        ("sub\\dir\\file.txt", "sub/dir/file.txt"),
        ("sub/dir\\file.txt", "sub/dir/file.txt"),
        (".\\sub\\", "./sub/"),
        ("out\\", "out/"),
        ("c:\\Users\\me", "C:/Users/me"),
        ("c:/Users/me", "C:/Users/me"),
        ("C:rel", "C:rel"),
        ("\\\\server\\share\\dir", "//server/share/dir"),
        (pathlib.PureWindowsPath("sub/dir/file.txt"), "sub/dir/file.txt"),
        (path_lib.Path("sub\\file.txt"), "sub/file.txt"),
        ("a", "a"),
        ("", ""),
    ],
)
def test_nt_path_constructor(arg, expected):
    path = _NtPath(arg)
    assert path == expected
    assert type(path) is _NtPath
    assert type(str(path)) is str


def test_nt_path_constructor_invalid():
    with pytest.raises(TypeError):
        _NtPath(None)


def test_nt_path_join():
    path = _NtPath("sub/dir")
    for result in path / "file.txt", path / _NtPath("file.txt"), path.joinpath("file.txt"):
        assert result == "sub/dir/file.txt"
        assert type(result) is _NtPath
    assert "top" / path == "top/sub/dir"
    assert path / "nested\\file.txt" == "sub/dir/nested/file.txt"
    assert path / pathlib.PureWindowsPath("a/b") == "sub/dir/a/b"
    assert path / "" == "sub/dir/"
    assert path + "\\file.txt" == "sub/dir/file.txt"
    assert "top\\" + path == "top/sub/dir"
    assert path / "C:/abs" == "C:/abs"


def test_nt_path_parts():
    path = _NtPath("C:/Users/me/proj/x.tar.gz")
    assert path.parent == "C:/Users/me/proj"
    assert path.name == "x.tar.gz"
    assert path.stem == "x.tar"
    assert path.suffix == ".gz"
    assert path.drive == "C:"
    assert path.with_suffix(".md") == "C:/Users/me/proj/x.tar.md"
    assert path.with_name("y") == "C:/Users/me/proj/y"
    assert path.splitall() == ["C:/", "Users", "me", "proj", "x.tar.gz"]
    assert path.splitpath() == ("C:/Users/me/proj", "x.tar.gz")
    assert path.splitdrive() == ("C:", "/Users/me/proj/x.tar.gz")
    assert path.splitext() == ("C:/Users/me/proj/x.tar", ".gz")
    # The parent of a directory directly under the root is the root, not the drive alone,
    # which would be a relative path.
    assert _NtPath("C:/Users").parent == "C:/"
    assert _NtPath("sub/file.txt").parent == "sub"
    assert _NtPath("file.txt").parent == ""


def test_nt_path_unc():
    path = _NtPath("//server/share/dir/file.txt")
    assert path.splitdrive() == ("//server/share", "/dir/file.txt")
    assert path.parent.parent == "//server/share/"
    assert path.isabs()


def test_nt_path_isabs():
    assert _NtPath("C:/Users").isabs()
    assert _NtPath("C:/").isabs()
    assert _NtPath("//server/share").isabs()
    # A root without a drive counts as absolute, in every Python version.
    assert _NtPath("/dir/file.txt").isabs()
    assert not _NtPath("sub/file.txt").isabs()
    assert not _NtPath("C:rel").isabs()
    assert not _NtPath("C:").isabs()
    assert not _NtPath("").isabs()


@pytest.mark.parametrize(
    ("arg", "expected"),
    [
        ("./a/../b/", "b"),
        ("a//b/./c", "a/b/c"),
        ("../x", "../x"),
        ("C:/a/../b", "C:/b"),
        (".", "."),
        ("a/..", "."),
    ],
)
def test_nt_path_normpath(arg, expected):
    result = _NtPath(arg).normpath()
    assert result == expected
    assert type(result) is _NtPath


@pytest.mark.parametrize(
    ("path", "start", "expected"),
    [
        ("C:/Users/me/proj/x.txt", "C:/Users/me", "proj/x.txt"),
        ("C:/Users/me", "C:/Users/me/proj/sub", "../.."),
        ("C:/Users/me", "C:/Users/me", "."),
        # Drive letters and path components are compared without regard to case.
        ("C:/Users/me/proj/x.txt", "c:/users/ME", "proj/x.txt"),
        # There is no relative path between two drives.
        ("C:/Users/me/x.txt", "D:/data", "C:/Users/me/x.txt"),
    ],
)
def test_nt_path_relpath(path, start, expected):
    result = _NtPath(path).relpath(start)
    assert result == expected
    assert type(result) is _NtPath


def test_nt_path_replace():
    """A substitution has the result of `str.replace`, without normalization."""
    path = _NtPath("C:/Users/me/x.txt")
    native = path.replace("/", "\\")
    assert native == "C:\\Users\\me\\x.txt"
    assert type(native) is _NtPath
    assert path.replace(".txt", ".md") == "C:/Users/me/x.md"
    assert path.replace("/", "\\", 1) == "C:\\Users/me/x.txt"
    # Passing the result to StepUp again restores its spelling.
    assert _NtPath(native) == path


def test_nt_path_module_plain_arguments():
    """The functions of the path module also work on instances of the class."""
    module = _NtPath.module
    paths = [_NtPath("C:/a/b/c"), _NtPath("C:/a/b/d")]
    assert module.commonpath(paths) == "C:/a/b"
    assert module.commonprefix(paths) == "C:/a/b/"
    assert module.relpath(paths[0], start=paths[1]) == "../c"
    assert module.split(paths[0]) == ("C:/a/b", "c")
    assert module.isabs(paths[0])
    assert module.sep == "/"


def test_nt_path_pathlib():
    """`pathlib` parses a path into the same components as it does for a plain string."""
    path = _NtPath("C:/Users/me/proj/x.tar.gz")
    pure = pathlib.PureWindowsPath(path)
    assert pure == pathlib.PureWindowsPath("C:/Users/me/proj/x.tar.gz")
    assert pure.name == "x.tar.gz"
    assert pure.stem == "x.tar"
    assert pure.parent == pathlib.PureWindowsPath("C:/Users/me/proj")
    assert pure.parts == ("C:\\", "Users", "me", "proj", "x.tar.gz")
    assert str(pure.relative_to("C:/Users/me")) == "proj\\x.tar.gz"
    assert (pathlib.PureWindowsPath("a") / _NtPath("b/c")).parts == ("a", "b", "c")
    assert pure.as_posix() == path


def test_nt_path_ntpath():
    """The functions of `ntpath` treat a path like a plain string."""
    paths = [_NtPath("C:/a/b/c"), _NtPath("C:/a/b/d")]
    assert ntpath.commonpath(paths) == "C:\\a\\b"
    assert ntpath.normpath(paths[0]) == "C:\\a\\b\\c"
    assert ntpath.basename(paths[0]) == "c"
    assert ntpath.dirname(paths[0]) == "C:/a/b"
    assert ntpath.splitdrive(_NtPath("//server/share/dir")) == ("//server/share", "/dir")
    assert _NtPath(ntpath.join(paths[0], "e")) == "C:/a/b/c/e"


def test_nt_path_upstream():
    """A path of the `path` library is normalized when it is given to StepUp."""
    upstream = path_lib.Path.using_module(ntpath)("sub") / "file.txt"
    assert upstream == "sub\\file.txt"
    assert _NtPath(upstream) == "sub/file.txt"
    assert _NtPath("top") / upstream == "top/sub/file.txt"


@pytest.mark.skipif(sys.platform != "win32", reason="Tests the paths of Windows.")
def test_path_windows(path_tmp):
    assert issubclass(Path, _NtPath)
    assert "\\" not in Path.cwd()
    assert Path.cwd() == Path(os.getcwd())
    assert Path("sub\\dir") / "file.txt" == "sub/dir/file.txt"
    assert "\\" not in path_tmp
    path = path_tmp / "sub" / "file.txt"
    path.parent.makedirs_p()
    path.write_text("hello", encoding="utf-8")
    assert "\\" not in path.realpath()
    assert path.absolute() == path
    assert path_tmp.glob("*/*.txt") == [path]
    assert list(path_tmp.walkfiles()) == [path]
    assert path.relpath(path_tmp) == "sub/file.txt"
    assert Path(str(path).lower()).relpath(path_tmp) == "sub/file.txt"
    assert Path("~/x").expanduser() == Path(os.path.expanduser("~")) / "x"
    # Other libraries see the same file.
    assert pathlib.Path(path).name == "file.txt"
    assert pathlib.Path(path).read_text(encoding="utf-8") == "hello"
    assert os.path.normpath(path) == str(path).replace("/", "\\")
    assert coerce_str(pathlib.Path("sub/dir")) == "sub/dir"
    assert coerce_path("sub\\dir\\") == "sub/dir/"
    assert get_affixes(".\\sub\\") == ("./", "/")


@pytest.mark.skipif(sys.platform == "win32", reason="Tests the paths of POSIX systems.")
def test_path_posix():
    """A backslash is an ordinary character in a file name."""
    assert not issubclass(Path, _NtPath)
    assert Path("sub\\name") == "sub\\name"
    assert Path("sub\\name").parent == ""
    assert coerce_str("sub\\name") == "sub\\name"
    assert Path("a/b").replace("/", "\\") == "a\\b"


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
