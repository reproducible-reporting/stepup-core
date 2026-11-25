# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Unit tests for stepup.core.stepinfo."""

import json

import attrs
import pytest

from stepup.core.path import Path
from stepup.core.stepinfo import StepInfo, dump_step_info, load_step_info


def test_step_info_initialization():
    step_info = StepInfo(
        command="echo 'Hello, World!'",
        workdir="/tmp",
        inp=["input1.txt", "input2.txt"],
        env=["ENV_VAR", 1],
        out=["output1.txt"],
        vol=["volatile1.txt"],
    )
    assert step_info.command == "echo 'Hello, World!'"
    assert step_info.workdir == Path("/tmp")
    assert step_info.inp == [Path("input1.txt"), Path("input2.txt")]
    assert step_info.env == ["1", "ENV_VAR"]
    assert step_info.out == [Path("output1.txt")]
    assert step_info.vol == [Path("volatile1.txt")]


def test_step_info_removes_duplicates():
    step_info = StepInfo(
        command="cat b a",
        workdir=".",
        inp=["b.txt", "a.txt", "b.txt"],
        env=["FOO", "BAR", "FOO"],
        out=["c.txt", "c.txt"],
        vol=["d.txt", "d.txt"],
    )
    assert step_info.inp == ["a.txt", "b.txt"]
    assert step_info.env == ["BAR", "FOO"]
    assert step_info.out == ["c.txt"]
    assert step_info.vol == ["d.txt"]


def test_load_step_info_single(path_tmp: Path):
    path_json = path_tmp / "test_step_info.json"
    data = {
        "command": "echo 'Hello, World!'",
        "workdir": "/tmp",
        "inp": ["input1.txt"],
        "env": ["ENV_VAR"],
        "out": ["output1.txt"],
        "vol": ["volatile1.txt"],
    }
    with open(path_json, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f)
    step_info = load_step_info(path_json)
    assert isinstance(step_info, StepInfo)
    assert attrs.asdict(step_info) == data


def test_load_step_info_multiple(path_tmp: Path):
    path_json = path_tmp / "test_step_info.json"
    data = [
        {
            "command": "echo 'Hello, World!'",
            "workdir": "/tmp",
            "inp": ["input1.txt"],
            "env": ["ENV_VAR1"],
            "out": ["output1.txt"],
            "vol": ["volatile1.txt"],
        },
        {
            "command": "echo 'Goodbye, World!'",
            "workdir": "/var",
            "inp": ["input2.txt"],
            "env": ["ENV_VAR2"],
            "out": ["output2.txt"],
            "vol": ["volatile2.txt"],
        },
    ]
    with open(path_json, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f)
    step_infos = load_step_info(path_json)
    assert isinstance(step_infos, list)
    assert len(step_infos) == 2
    assert [attrs.asdict(si) for si in step_infos] == data


def test_dump_step_info(path_tmp: Path):
    path_json = path_tmp / "test_step_info.json"
    step_info = StepInfo(
        command="echo 'Hello, World!'",
        workdir=Path("/tmp"),
        inp=["input1.txt"],
        env=["ENV_VAR"],
        out=["output1.txt"],
        vol=["volatile1.txt"],
    )
    dump_step_info(path_json, step_info)
    with open(path_json, encoding="utf-8") as f:
        data = json.load(f)
    assert data["command"] == "echo 'Hello, World!'"
    assert data["workdir"] == "/tmp"
    assert data["inp"] == ["input1.txt"]
    assert data["env"] == ["ENV_VAR"]
    assert data["out"] == ["output1.txt"]
    assert data["vol"] == ["volatile1.txt"]


def test_dump_step_info_multiple(path_tmp: Path):
    path_json = path_tmp / "test_step_info.json"
    step_infos = [
        StepInfo(
            command="echo 'Hello, World!'",
            workdir=Path("/tmp"),
            inp=["input1.txt"],
            env=["ENV_VAR1"],
            out=["output1.txt"],
            vol=["volatile1.txt"],
        ),
        StepInfo(
            command="echo 'Goodbye, World!'",
            workdir=Path("/var"),
            inp=["input2.txt"],
            env=["ENV_VAR2"],
            out=["output2.txt"],
            vol=["volatile2.txt"],
        ),
    ]
    dump_step_info(path_json, step_infos)
    with open(path_json, encoding="utf-8") as f:
        data = json.load(f)
    assert len(data) == 2
    assert data[0]["command"] == "echo 'Hello, World!'"
    assert data[0]["workdir"] == "/tmp"
    assert data[0]["inp"] == ["input1.txt"]
    assert data[0]["env"] == ["ENV_VAR1"]
    assert data[0]["out"] == ["output1.txt"]
    assert data[0]["vol"] == ["volatile1.txt"]
    assert data[1]["command"] == "echo 'Goodbye, World!'"
    assert data[1]["workdir"] == "/var"
    assert data[1]["inp"] == ["input2.txt"]
    assert data[1]["env"] == ["ENV_VAR2"]
    assert data[1]["out"] == ["output2.txt"]
    assert data[1]["vol"] == ["volatile2.txt"]


def test_filter_inp():
    step_info = StepInfo(
        command="echo 'Hello, World!'",
        workdir=Path("/tmp"),
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
        workdir=Path("/tmp"),
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
        workdir=Path("/tmp"),
        inp=["input1.txt"],
        env=["ENV_VAR"],
        out=["output1.txt"],
        vol=["volatile1.txt", "volatile2.log"],
    )
    filtered = list(step_info.filter_vol("*.txt"))
    assert len(filtered) == 1
    assert filtered[0] == Path("volatile1.txt")


def test_step_info_defaults():
    step_info = StepInfo()
    assert step_info.command is None
    assert step_info.inp == []
    assert step_info.env == []
    assert step_info.out == []
    assert step_info.vol == []
    assert step_info.workdir == Path(".")
    assert isinstance(step_info.workdir, Path)


def test_step_info_keywords_only():
    step_info = StepInfo(inp=["b", "a"], workdir="sub")
    assert step_info.command is None
    assert step_info.inp == ["a", "b"]
    assert step_info.workdir == "sub"


def test_dump_step_info_without_command(path_tmp: Path):
    path_json = path_tmp / "test_step_info.json"
    dump_step_info(path_json, StepInfo(inp=["a.txt"], workdir="sub"))
    with open(path_json, encoding="utf-8") as f:
        data = json.load(f)
    assert list(data) == ["inp", "env", "out", "vol", "workdir"]
    assert data["inp"] == ["a.txt"]
    assert data["workdir"] == "sub"


def test_dump_step_info_with_command_first(path_tmp: Path):
    path_json = path_tmp / "test_step_info.json"
    dump_step_info(path_json, StepInfo("cat a.txt", inp=["a.txt"]))
    with open(path_json, encoding="utf-8") as f:
        data = json.load(f)
    assert list(data) == ["command", "inp", "env", "out", "vol", "workdir"]


def test_load_step_info_without_command(path_tmp: Path):
    path_json = path_tmp / "test_step_info.json"
    data = {"inp": ["a.txt"], "env": [], "out": [], "vol": [], "workdir": "sub"}
    with open(path_json, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f)
    step_info = load_step_info(path_json)
    assert step_info == StepInfo(inp=["a.txt"], workdir="sub")
    with open(path_json, "w", encoding="utf-8", newline="\n") as f:
        json.dump([data, {**data, "command": None}], f)
    step_infos = load_step_info(path_json)
    assert step_infos == [StepInfo(inp=["a.txt"], workdir="sub")] * 2


def test_step_info_round_trip_mixed(path_tmp: Path):
    path_json = path_tmp / "test_step_info.json"
    step_infos = [
        StepInfo("cat a.txt", inp=["a.txt"], env=["FOO"], out=["b.txt"], workdir="sub"),
        StepInfo(inp=["c.txt"], vol=["d.txt"]),
    ]
    dump_step_info(path_json, step_infos)
    with open(path_json, encoding="utf-8") as f:
        data = json.load(f)
    assert "command" in data[0]
    assert "command" not in data[1]
    assert load_step_info(path_json) == step_infos


@pytest.fixture
def stepup_root(monkeypatch, path_tmp: Path) -> Path:
    """A StepUp root with a few subdirectories, entered from a directory with another name."""
    root = path_tmp / "root"
    for subdir in ["sub", "other", "sub/deep"]:
        (root / subdir).makedirs()
    (path_tmp / "ext").makedirs()
    monkeypatch.setenv("STEPUP_ROOT", root)
    monkeypatch.chdir(root)
    return root


def test_rebase_same_workdir_only_normalizes(stepup_root: Path):
    step_info = StepInfo(inp=["./a.txt", "x/../b.txt"], out=["./c.txt"], workdir="./sub")
    assert step_info.rebase("sub") == StepInfo(inp=["a.txt", "b.txt"], out=["c.txt"], workdir="sub")


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
def test_rebase(monkeypatch, stepup_root: Path, old, new, paths, expected, cwd):
    monkeypatch.chdir(stepup_root / cwd)
    step_info = StepInfo(inp=paths, out=paths, vol=paths, workdir=old)
    assert step_info.rebase(new) == StepInfo(inp=expected, out=expected, vol=expected, workdir=new)


def test_rebase_keeps_command_and_env(stepup_root: Path):
    step_info = StepInfo("cat a.txt", inp=["a.txt"], env=["FOO", "BAR"], workdir="sub")
    rebased = step_info.rebase(".")
    assert rebased.command == "cat a.txt"
    assert rebased.env == ["BAR", "FOO"]
    assert rebased.inp == ["sub/a.txt"]


def test_rebase_absolute_paths(stepup_root: Path):
    path_abs = stepup_root.parent / "ext/a.txt"
    step_info = StepInfo(inp=[path_abs, "b.txt"], workdir="sub")
    assert step_info.rebase("other").inp == sorted([path_abs, Path("../sub/b.txt")])
    rebased = step_info.rebase(stepup_root.parent / "ext")
    assert rebased.workdir == stepup_root.parent / "ext"
    assert rebased.inp == ["../root/sub/b.txt", path_abs]
    assert rebased.rebase("sub") == step_info
