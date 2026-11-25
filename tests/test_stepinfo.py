# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Unit tests for stepup.core.stepinfo."""

import contextlib
import json
import pickle

import attrs
import pytest

from stepup.core.cattrs import json_converter
from stepup.core.exceptions import StepUpError
from stepup.core.path import Path, anchor_path
from stepup.core.stepinfo import StepInfo, dump_step_info, load_step_info


@pytest.fixture
def project(monkeypatch, path_tmp: Path) -> Path:
    """A project with a few subdirectories, entered from a directory with another name.

    `STEPUP_ROOT` is not set, because only an absolute working directory depends on it.
    """
    root = path_tmp / "root"
    for subdir in ["sub", "other", "sub/deep"]:
        (root / subdir).makedirs()
    (path_tmp / "ext").makedirs()
    monkeypatch.delenv("STEPUP_ROOT", raising=False)
    monkeypatch.chdir(root)
    return root


def test_step_info_initialization(project: Path):
    step_info = StepInfo(
        command="echo 'Hello, World!'",
        abs_workdir=project / "sub",
        inp=["input1.txt", "input2.txt"],
        env=["ENV_VAR", 1],
        out=["output1.txt"],
        vol=["volatile1.txt"],
    )
    assert step_info.command == "echo 'Hello, World!'"
    assert step_info.abs_workdir == project / "sub"
    assert step_info.is_abs is False
    assert step_info.workdir == Path("sub")
    assert isinstance(step_info.workdir, Path)
    assert step_info.inp == [Path("input1.txt"), Path("input2.txt")]
    assert step_info.env == ["1", "ENV_VAR"]
    assert step_info.out == [Path("output1.txt")]
    assert step_info.vol == [Path("volatile1.txt")]


def test_step_info_removes_duplicates():
    step_info = StepInfo(
        command="cat b a",
        inp=["b.txt", "a.txt", "b.txt"],
        env=["FOO", "BAR", "FOO"],
        out=["c.txt", "c.txt"],
        vol=["d.txt", "d.txt"],
    )
    assert step_info.inp == ["a.txt", "b.txt"]
    assert step_info.env == ["BAR", "FOO"]
    assert step_info.out == ["c.txt"]
    assert step_info.vol == ["d.txt"]


def test_step_info_defaults(project: Path):
    step_info = StepInfo()
    assert step_info.command is None
    assert step_info.inp == []
    assert step_info.env == []
    assert step_info.out == []
    assert step_info.vol == []
    assert step_info.abs_workdir == project
    assert step_info.is_abs is False
    assert step_info.workdir == Path(".")
    assert isinstance(step_info.workdir, Path)


def test_step_info_keywords_only(project: Path):
    step_info = StepInfo(inp=["b", "a"], abs_workdir=project / "sub")
    assert step_info.command is None
    assert step_info.inp == ["a", "b"]
    assert step_info.workdir == "sub"


def test_step_info_normalizes_abs_workdir(project: Path):
    step_info = StepInfo(abs_workdir=project / "sub/../other/./")
    assert step_info.abs_workdir == project / "other"
    assert step_info.workdir == "other"


def test_step_info_rejects_relative_abs_workdir():
    with pytest.raises(ValueError, match="abs_workdir must be an absolute path"):
        StepInfo(abs_workdir="sub")
    with pytest.raises(ValueError, match="abs_workdir must be an absolute path"):
        StepInfo("cat a.txt", ["a.txt"], [], [], [], ".")


def test_step_info_rejects_workdir():
    with pytest.raises(TypeError):
        StepInfo(workdir="sub")
    with pytest.raises(TypeError):
        attrs.evolve(StepInfo(), workdir="sub")


def test_workdir_follows_current_directory(project: Path):
    step_info = StepInfo(inp=["a.txt"], abs_workdir=project / "sub")
    with contextlib.chdir("sub/deep"):
        assert step_info.workdir == ".."
    with contextlib.chdir("../ext"):
        assert step_info.workdir == "../root/sub"
    assert step_info.workdir == "sub"


def test_workdir_absolute_stays_absolute(project: Path):
    step_info = StepInfo(inp=["a.txt"], abs_workdir=project / "sub", is_abs=True)
    assert step_info.workdir == project / "sub"
    with contextlib.chdir("sub/deep"):
        assert step_info.workdir == project / "sub"


def test_dot_in_different_directories_not_equal(project: Path):
    step_info = StepInfo(inp=["a.txt"])
    with contextlib.chdir("sub"):
        other = StepInfo(inp=["a.txt"])
        assert other.workdir == "."
    assert step_info.workdir == "."
    assert step_info != other
    assert other.workdir == "sub"


def test_pickle_in_other_directory(project: Path):
    step_info = StepInfo("cat a.txt", inp=["a.txt"], abs_workdir=project / "sub")
    data = pickle.dumps(step_info)
    with contextlib.chdir("other"):
        loaded = pickle.loads(data)
        assert loaded == step_info
        assert loaded.workdir == "../sub"


def test_json_converter_in_other_directory(project: Path):
    step_info = StepInfo(inp=["a.txt"], out=["b.txt"], abs_workdir=project / "sub")
    data = json.loads(json.dumps(json_converter.unstructure(step_info)))
    assert data == {
        "inp": ["a.txt"],
        "env": [],
        "out": ["b.txt"],
        "vol": [],
        "abs_workdir": project / "sub",
        "is_abs": False,
    }
    with contextlib.chdir("other"):
        loaded = json_converter.structure(data, StepInfo)
        assert loaded == step_info
        assert loaded.workdir == "../sub"


#
# Files
#


def test_load_step_info_single(project: Path):
    path_abs = project.parent / "abs"
    data = {
        "command": "echo 'Hello, World!'",
        "workdir": path_abs,
        "inp": ["input1.txt"],
        "env": ["ENV_VAR"],
        "out": ["output1.txt"],
        "vol": ["volatile1.txt"],
    }
    with open("test_step_info.json", "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f)
    step_info = load_step_info("test_step_info.json")
    assert isinstance(step_info, StepInfo)
    assert step_info == StepInfo(
        "echo 'Hello, World!'",
        ["input1.txt"],
        ["ENV_VAR"],
        ["output1.txt"],
        ["volatile1.txt"],
        path_abs,
        True,
    )
    assert step_info.workdir == path_abs


def test_load_step_info_multiple(project: Path):
    data = [
        {
            "command": "echo 'Hello, World!'",
            "workdir": "sub",
            "inp": ["input1.txt"],
            "env": ["ENV_VAR1"],
            "out": ["output1.txt"],
            "vol": ["volatile1.txt"],
        },
        {
            "command": "echo 'Goodbye, World!'",
            "workdir": "../other",
            "inp": ["input2.txt"],
            "env": ["ENV_VAR2"],
            "out": ["output2.txt"],
            "vol": ["volatile2.txt"],
        },
    ]
    (project / "out").makedirs()
    with open("out/test_step_info.json", "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f)
    step_infos = load_step_info("out/test_step_info.json")
    assert isinstance(step_infos, list)
    assert step_infos == [
        StepInfo(
            "echo 'Hello, World!'",
            ["input1.txt"],
            ["ENV_VAR1"],
            ["output1.txt"],
            ["volatile1.txt"],
            project / "out/sub",
        ),
        StepInfo(
            "echo 'Goodbye, World!'",
            ["input2.txt"],
            ["ENV_VAR2"],
            ["output2.txt"],
            ["volatile2.txt"],
            project / "other",
        ),
    ]
    assert [si.workdir for si in step_infos] == ["out/sub", "other"]


def test_dump_step_info(project: Path):
    path_abs = project.parent / "abs"
    step_info = StepInfo(
        command="echo 'Hello, World!'",
        abs_workdir=path_abs,
        is_abs=True,
        inp=["input1.txt"],
        env=["ENV_VAR"],
        out=["output1.txt"],
        vol=["volatile1.txt"],
    )
    dump_step_info("test_step_info.json", step_info)
    with open("test_step_info.json", encoding="utf-8") as f:
        data = json.load(f)
    assert data == {
        "command": "echo 'Hello, World!'",
        "inp": ["input1.txt"],
        "env": ["ENV_VAR"],
        "out": ["output1.txt"],
        "vol": ["volatile1.txt"],
        "workdir": path_abs,
    }
    assert load_step_info("test_step_info.json") == step_info


def test_dump_step_info_multiple(project: Path):
    step_infos = [
        StepInfo(
            command="echo 'Hello, World!'",
            abs_workdir=project / "sub",
            inp=["input1.txt"],
            env=["ENV_VAR1"],
            out=["output1.txt"],
            vol=["volatile1.txt"],
        ),
        StepInfo(
            command="echo 'Goodbye, World!'",
            abs_workdir=project / "other",
            inp=["input2.txt"],
            env=["ENV_VAR2"],
            out=["output2.txt"],
            vol=["volatile2.txt"],
        ),
    ]
    dump_step_info("sub/test_step_info.json", step_infos)
    with open("sub/test_step_info.json", encoding="utf-8") as f:
        data = json.load(f)
    assert len(data) == 2
    assert data[0]["command"] == "echo 'Hello, World!'"
    assert data[0]["workdir"] == "."
    assert data[0]["inp"] == ["input1.txt"]
    assert data[0]["env"] == ["ENV_VAR1"]
    assert data[0]["out"] == ["output1.txt"]
    assert data[0]["vol"] == ["volatile1.txt"]
    assert data[1]["command"] == "echo 'Goodbye, World!'"
    assert data[1]["workdir"] == "../other"
    assert data[1]["inp"] == ["input2.txt"]
    assert data[1]["env"] == ["ENV_VAR2"]
    assert data[1]["out"] == ["output2.txt"]
    assert data[1]["vol"] == ["volatile2.txt"]
    assert load_step_info("sub/test_step_info.json") == step_infos


def test_dump_step_info_without_command(project: Path):
    dump_step_info("test_step_info.json", StepInfo(inp=["a.txt"], abs_workdir=project / "sub"))
    with open("test_step_info.json", encoding="utf-8") as f:
        data = json.load(f)
    assert list(data) == ["inp", "env", "out", "vol", "workdir"]
    assert data["inp"] == ["a.txt"]
    assert data["workdir"] == "sub"


def test_dump_step_info_with_command_first(project: Path):
    dump_step_info("test_step_info.json", StepInfo("cat a.txt", inp=["a.txt"]))
    with open("test_step_info.json", encoding="utf-8") as f:
        data = json.load(f)
    assert list(data) == ["command", "inp", "env", "out", "vol", "workdir"]
    assert data["workdir"] == "."


def test_load_step_info_without_command(project: Path):
    data = {"inp": ["a.txt"], "env": [], "out": [], "vol": [], "workdir": "sub"}
    with open("test_step_info.json", "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f)
    expected = StepInfo(inp=["a.txt"], abs_workdir=project / "sub")
    assert load_step_info("test_step_info.json") == expected
    with open("test_step_info.json", "w", encoding="utf-8", newline="\n") as f:
        json.dump([data, {**data, "command": None}], f)
    assert load_step_info("test_step_info.json") == [expected] * 2


def test_step_info_round_trip_mixed(project: Path):
    step_infos = [
        StepInfo(
            "cat a.txt", inp=["a.txt"], env=["FOO"], out=["b.txt"], abs_workdir=project / "sub"
        ),
        StepInfo(inp=["c.txt"], vol=["d.txt"]),
    ]
    dump_step_info("test_step_info.json", step_infos)
    with open("test_step_info.json", encoding="utf-8") as f:
        data = json.load(f)
    assert "command" in data[0]
    assert "command" not in data[1]
    assert load_step_info("test_step_info.json") == step_infos


def test_dump_step_info_relative_to_file(project: Path):
    (project / "out").makedirs()
    step_info = StepInfo(inp=["a.txt"], abs_workdir=project / "sub")
    dump_step_info("out/info.json", step_info)
    with open("out/info.json", encoding="utf-8") as f:
        assert json.load(f)["workdir"] == "../sub"
    loaded = load_step_info("out/info.json")
    assert loaded == step_info
    assert loaded.workdir == "sub"


def test_load_step_info_spelling_of_filename(project: Path):
    (project / "out").makedirs()
    step_info = StepInfo(inp=["a.txt"], abs_workdir=project / "sub")
    dump_step_info("out/info.json", step_info)
    loaded_abs = load_step_info(project / "out/info.json")
    with contextlib.chdir("sub/deep"):
        loaded_rel = load_step_info("../../out/info.json")
        assert loaded_rel.workdir == ".."
    assert loaded_abs == loaded_rel == step_info
    assert loaded_abs.is_abs is False


def test_load_step_info_without_workdir(project: Path):
    with open("sub/info.json", "w", encoding="utf-8", newline="\n") as f:
        json.dump({"inp": ["a.txt"]}, f)
    loaded = load_step_info("sub/info.json")
    assert loaded == StepInfo(inp=["a.txt"], abs_workdir=project / "sub")


@pytest.mark.parametrize("key", ["abs_workdir", "is_abs"])
def test_load_step_info_rejects_exchange_format(project: Path, key: str):
    data = json_converter.unstructure(StepInfo(inp=["a.txt"]))
    data = {"inp": ["a.txt"], key: data[key]}
    with open("info.json", "w", encoding="utf-8", newline="\n") as f:
        json.dump([data], f)
    with pytest.raises(StepUpError, match=r"info\.json"):
        load_step_info("info.json")


@pytest.mark.requires_symlinks
def test_load_step_info_through_symlink(monkeypatch, path_tmp: Path):
    (path_tmp / "real/proj/data").makedirs()
    (path_tmp / "link").symlink_to(path_tmp / "real")
    monkeypatch.chdir(path_tmp / "real/proj")
    with open("data/info.json", "w", encoding="utf-8", newline="\n") as f:
        json.dump({"inp": ["x.txt"], "workdir": "../work"}, f)
    loaded_rel = load_step_info("data/info.json")
    loaded_abs = load_step_info(path_tmp / "link/proj/data/info.json")
    assert loaded_rel.workdir == "work"
    assert loaded_abs.workdir == "work"
    assert loaded_abs == loaded_rel


#
# Rebase and merge
#


@pytest.mark.parametrize(
    ("old", "new", "paths", "expected"),
    [
        ("sub", "other", ["a.txt", "../b.txt"], ["../b.txt", "../sub/a.txt"]),
        ("sub", ".", ["a.txt", "../b.txt"], ["b.txt", "sub/a.txt"]),
        (".", "sub/deep", ["a.txt", "sub/b.txt"], ["../../a.txt", "../b.txt"]),
        (".", "../ext", ["a.txt"], ["../root/a.txt"]),
        ("../ext", ".", ["a.txt"], ["../ext/a.txt"]),
    ],
)
@pytest.mark.parametrize("cwd", [".", "sub/deep", "../ext"])
@pytest.mark.parametrize("stepup_root", [None, "sub", "../ext"])
def test_rebase(monkeypatch, project: Path, old, new, paths, expected, cwd, stepup_root):
    """The working directories are given relative to `project`, the test runs in `cwd`."""
    if stepup_root is not None:
        monkeypatch.setenv("STEPUP_ROOT", project / stepup_root)
    step_info = StepInfo(inp=paths, out=paths, vol=paths, abs_workdir=anchor_path(old))
    monkeypatch.chdir(project / cwd)
    new_workdir = (project / new).normpath().relpath()
    assert step_info.rebase(new_workdir) == StepInfo(
        inp=expected, out=expected, vol=expected, abs_workdir=(project / new).normpath()
    )


def test_rebase_same_workdir_only_normalizes(project: Path):
    step_info = StepInfo(
        inp=["./a.txt", "x/../b.txt"], out=["./c.txt"], abs_workdir=project / "sub"
    )
    assert step_info.rebase("./sub") == StepInfo(
        inp=["a.txt", "b.txt"], out=["c.txt"], abs_workdir=project / "sub"
    )


def test_rebase_keeps_command_and_env(project: Path):
    step_info = StepInfo(
        "cat a.txt", inp=["a.txt"], env=["FOO", "BAR"], abs_workdir=project / "sub"
    )
    rebased = step_info.rebase(".")
    assert rebased.command == "cat a.txt"
    assert rebased.env == ["BAR", "FOO"]
    assert rebased.inp == ["sub/a.txt"]


def test_rebase_absolute_paths(monkeypatch, project: Path):
    monkeypatch.setenv("STEPUP_ROOT", project)
    path_abs = project.parent / "ext/a.txt"
    step_info = StepInfo(inp=[path_abs, "b.txt"], abs_workdir=project / "sub")
    assert step_info.rebase("other").inp == sorted([path_abs, Path("../sub/b.txt")])
    rebased = step_info.rebase(project.parent / "ext")
    assert rebased.workdir == project.parent / "ext"
    assert rebased.is_abs is True
    assert rebased.inp == ["../root/sub/b.txt", path_abs]
    assert rebased.rebase("sub") == step_info


@pytest.mark.requires_symlinks
def test_rebase_absolute_workdir_through_symlink(monkeypatch, path_tmp: Path):
    """An absolute workdir spelled through the symbolic link of `STEPUP_ROOT` is recognized."""
    (path_tmp / "real/proj/sub").makedirs()
    (path_tmp / "link").symlink_to(path_tmp / "real")
    monkeypatch.setenv("STEPUP_ROOT", path_tmp / "link/proj")
    monkeypatch.chdir(path_tmp / "link/proj")
    step_info = StepInfo(inp=["b.txt"], abs_workdir=path_tmp / "link/proj/sub", is_abs=True)
    rebased = step_info.rebase(".")
    assert rebased.inp == ["sub/b.txt"]
    assert rebased.workdir == "."
    assert rebased.rebase(path_tmp / "link/proj/sub") == step_info


def test_merge_keeps_command_and_workdir(project: Path):
    step_info = StepInfo("./work.py", inp=["work.py"], abs_workdir=project / "sub")
    other = StepInfo(None, out=["c.txt"], abs_workdir=project / "other")
    assert step_info.merge(other) == StepInfo(
        "./work.py", inp=["work.py"], out=["../other/c.txt"], abs_workdir=project / "sub"
    )
    assert other.merge(step_info).command is None


def test_merge_subdirectory(project: Path):
    step_info = StepInfo("./work.py", inp=["sub/a.txt", "work.py"], out=["./d.txt"])
    other = StepInfo(
        None, inp=["a.txt", "b.txt"], out=["../d.txt"], vol=["v.txt"], abs_workdir=project / "sub"
    )
    assert step_info.merge(other) == StepInfo(
        "./work.py",
        inp=["sub/a.txt", "sub/b.txt", "work.py"],
        out=["d.txt"],
        vol=["sub/v.txt"],
    )


def test_merge_env(project: Path):
    step_info = StepInfo(env=["FOO", "BAR"], abs_workdir=project / "sub")
    other = StepInfo(env=["BAR", "EGG"], abs_workdir=project / "other")
    assert step_info.merge(other).env == ["BAR", "EGG", "FOO"]


def test_merge_without_others_normalizes(project: Path):
    step_info = StepInfo(
        "cat a.txt", inp=["./a.txt", "x/../a.txt"], env=["FOO"], abs_workdir=project / "sub"
    )
    assert step_info.merge() == StepInfo(
        "cat a.txt", inp=["a.txt"], env=["FOO"], abs_workdir=project / "sub"
    )


def test_merge_into_empty(project: Path):
    a = StepInfo("./a.py", inp=["a.txt"], abs_workdir=project / "sub")
    b = StepInfo(None, out=["b.txt"], abs_workdir=project / "sub/deep")
    with contextlib.chdir("other"):
        merged = StepInfo().merge(a, b)
    assert merged == StepInfo(
        inp=["../sub/a.txt"], out=["../sub/deep/b.txt"], abs_workdir=project / "other"
    )


def test_merge_created_in_different_directories(project: Path):
    a = StepInfo(inp=["a.txt"])
    with contextlib.chdir("sub"):
        b = StepInfo(inp=["b.txt"])
    with contextlib.chdir("sub/deep"):
        merged = a.merge(b)
        assert merged.workdir == "../.."
    assert merged == StepInfo(inp=["a.txt", "sub/b.txt"])


def test_merge_absolute_paths(project: Path):
    path_abs = project.parent / "ext/a.txt"
    step_info = StepInfo(inp=["b.txt"], abs_workdir=project / "sub")
    other = StepInfo(inp=[path_abs, "b.txt"], abs_workdir=project / "other")
    assert step_info.merge(other).inp == sorted([path_abs, Path("../other/b.txt"), Path("b.txt")])


#
# Filters
#


def test_filter_inp():
    step_info = StepInfo(
        command="echo 'Hello, World!'",
        inp=["input1.txt", "input2.log"],
        env=["ENV_VAR"],
        out=["output1.txt"],
        vol=["volatile1.txt"],
    )
    filtered = list(step_info.filter_inp("*.txt"))
    assert len(filtered) == 1
    assert filtered[0] == Path("input1.txt")


def test_filter_out():
    step_info = StepInfo(
        command="echo 'Hello, World!'",
        inp=["input1.txt"],
        env=["ENV_VAR"],
        out=["output1.txt", "output2.log"],
        vol=["volatile1.txt"],
    )
    filtered = list(step_info.filter_out("*.txt"))
    assert len(filtered) == 1
    assert filtered[0] == Path("output1.txt")


def test_filter_vol():
    step_info = StepInfo(
        command="echo 'Hello, World!'",
        inp=["input1.txt"],
        env=["ENV_VAR"],
        out=["output1.txt"],
        vol=["volatile1.txt", "volatile2.log"],
    )
    filtered = list(step_info.filter_vol("*.txt"))
    assert len(filtered) == 1
    assert filtered[0] == Path("volatile1.txt")
