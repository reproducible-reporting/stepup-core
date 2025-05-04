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
"""Unit tests for stepup.core.stepinfo."""

import json

import attrs
from path import Path

from stepup.core.stepinfo import StepInfo, dump_step_info, load_step_info


def test_step_info_initialization():
    step_info = StepInfo(
        action="echo 'Hello, World!'",
        workdir="/tmp",
        inp=["input1.txt", "input2.txt"],
        env=["ENV_VAR", 1],
        out=["output1.txt"],
        vol=["volatile1.txt"],
    )
    assert step_info.action == "echo 'Hello, World!'"
    assert step_info.workdir == Path("/tmp")
    assert step_info.inp == [Path("input1.txt"), Path("input2.txt")]
    assert step_info.env == ["1", "ENV_VAR"]
    assert step_info.out == [Path("output1.txt")]
    assert step_info.vol == [Path("volatile1.txt")]


def test_load_step_info_single(path_tmp: Path):
    path_json = path_tmp / "test_step_info.json"
    data = {
        "action": "echo 'Hello, World!'",
        "workdir": "/tmp",
        "inp": ["input1.txt"],
        "env": ["ENV_VAR"],
        "out": ["output1.txt"],
        "vol": ["volatile1.txt"],
    }
    with open(path_json, "w") as f:
        json.dump(data, f)
    step_info = load_step_info(path_json)
    assert isinstance(step_info, StepInfo)
    assert attrs.asdict(step_info) == data


def test_load_step_info_multiple(path_tmp: Path):
    path_json = path_tmp / "test_step_info.json"
    data = [
        {
            "action": "echo 'Hello, World!'",
            "workdir": "/tmp",
            "inp": ["input1.txt"],
            "env": ["ENV_VAR1"],
            "out": ["output1.txt"],
            "vol": ["volatile1.txt"],
        },
        {
            "action": "echo 'Goodbye, World!'",
            "workdir": "/var",
            "inp": ["input2.txt"],
            "env": ["ENV_VAR2"],
            "out": ["output2.txt"],
            "vol": ["volatile2.txt"],
        },
    ]
    with open(path_json, "w") as f:
        json.dump(data, f)
    step_infos = load_step_info(path_json)
    assert isinstance(step_infos, list)
    assert len(step_infos) == 2
    assert [attrs.asdict(si) for si in step_infos] == data


def test_dump_step_info(path_tmp: Path):
    path_json = path_tmp / "test_step_info.json"
    step_info = StepInfo(
        action="echo 'Hello, World!'",
        workdir=Path("/tmp"),
        inp=["input1.txt"],
        env=["ENV_VAR"],
        out=["output1.txt"],
        vol=["volatile1.txt"],
    )
    dump_step_info(path_json, step_info)
    with open(path_json) as f:
        data = json.load(f)
    assert data["action"] == "echo 'Hello, World!'"
    assert data["workdir"] == "/tmp"
    assert data["inp"] == ["input1.txt"]
    assert data["env"] == ["ENV_VAR"]
    assert data["out"] == ["output1.txt"]
    assert data["vol"] == ["volatile1.txt"]


def test_dump_step_info_multiple(path_tmp: Path):
    path_json = path_tmp / "test_step_info.json"
    step_infos = [
        StepInfo(
            action="echo 'Hello, World!'",
            workdir=Path("/tmp"),
            inp=["input1.txt"],
            env=["ENV_VAR1"],
            out=["output1.txt"],
            vol=["volatile1.txt"],
        ),
        StepInfo(
            action="echo 'Goodbye, World!'",
            workdir=Path("/var"),
            inp=["input2.txt"],
            env=["ENV_VAR2"],
            out=["output2.txt"],
            vol=["volatile2.txt"],
        ),
    ]
    dump_step_info(path_json, step_infos)
    with open(path_json) as f:
        data = json.load(f)
    assert len(data) == 2
    assert data[0]["action"] == "echo 'Hello, World!'"
    assert data[0]["workdir"] == "/tmp"
    assert data[0]["inp"] == ["input1.txt"]
    assert data[0]["env"] == ["ENV_VAR1"]
    assert data[0]["out"] == ["output1.txt"]
    assert data[0]["vol"] == ["volatile1.txt"]
    assert data[1]["action"] == "echo 'Goodbye, World!'"
    assert data[1]["workdir"] == "/var"
    assert data[1]["inp"] == ["input2.txt"]
    assert data[1]["env"] == ["ENV_VAR2"]
    assert data[1]["out"] == ["output2.txt"]
    assert data[1]["vol"] == ["volatile2.txt"]


def test_filter_inp():
    step_info = StepInfo(
        action="echo 'Hello, World!'",
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
        action="echo 'Hello, World!'",
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
        action="echo 'Hello, World!'",
        workdir=Path("/tmp"),
        inp=["input1.txt"],
        env=["ENV_VAR"],
        out=["output1.txt"],
        vol=["volatile1.txt", "volatile2.log"],
    )
    filtered = list(step_info.filter_vol("*.txt"))
    assert len(filtered) == 1
    assert filtered[0] == Path("volatile1.txt")
