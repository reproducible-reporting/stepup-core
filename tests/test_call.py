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
"""Unit tests for stepup.core.call and the call() API function in stepup.core.api."""

import argparse
import json
import shlex
from dataclasses import dataclass

import pytest
import yaml

from stepup.core.api import call
from stepup.core.call import _dispatch, _print_list, driver
from stepup.core.enums import Need
from stepup.core.stepinfo import StepInfo

# ---------------------------------------------------------------------------
# Module-level helper used in type-coercion tests
# ---------------------------------------------------------------------------


@dataclass
class _SampleDC:
    a: int


# ---------------------------------------------------------------------------
# Fixture for call() API tests: mocks step() and amend()
# ---------------------------------------------------------------------------


@pytest.fixture
def captured(monkeypatch):
    """Mock step() and amend() for call() unit tests.

    Returns the list of dicts recorded for each step() call.
    """
    calls = []

    def mock_step(
        command,
        *,
        inp=(),
        env=(),
        out=(),
        vol=(),
        workdir="./",
        need=Need.DEFAULT,
        resources=None,
        shell=False,
    ):
        calls.append(
            {
                "command": command,
                "inp": list(inp),
                "env": list(env),
                "out": list(out),
                "vol": list(vol),
                "workdir": workdir,
                "need": need,
                "resources": resources,
            }
        )
        return StepInfo(command, workdir, list(inp), list(env), list(out), list(vol))

    monkeypatch.setattr("stepup.core.api.step", mock_step)
    monkeypatch.setattr("stepup.core.api.amend", lambda **kw: None)
    return calls


# ===========================================================================
# Section 1: _dispatch / _print_list unit tests  (no director needed)
# ===========================================================================

# --- Basic dispatch ---


def test_function_called_with_kwargs():
    received = []

    def fn(x, y):
        received.append((x, y))

    args = argparse.Namespace(function="fn", json_inp='{"x": 1, "y": "hello"}', path_inp=None)
    _dispatch("s.py", {"fn": fn}, args)
    assert received == [(1, "hello")]


def test_no_arguments():
    # _dispatch always injects inp/out into the forwarded dict;
    # a function with no matching params receives neither.
    called = []

    def fn():
        called.append(True)

    args = argparse.Namespace(function="fn", json_inp='{"inp": [], "out": []}', path_inp=None)
    _dispatch("s.py", {"fn": fn}, args)
    assert called == [True]


def test_function_via_commandline_json():
    received = []

    def fn(x):
        received.append(x)

    args = argparse.Namespace(function="fn", json_inp='{"x": 42}', path_inp=None)
    _dispatch("s.py", {"fn": fn}, args)
    assert received == [42]


def test_function_via_inp_json_file(monkeypatch, path_tmp):
    args_file = path_tmp / "args.json"
    with open(args_file, "w") as fh:
        json.dump({"x": 7}, fh)
    received = []

    def fn(x):
        received.append(x)

    monkeypatch.setattr("stepup.core.api.amend", lambda **kw: None)
    args = argparse.Namespace(function="fn", json_inp=None, path_inp=str(args_file))
    _dispatch("s.py", {"fn": fn}, args)
    assert received == [7]


def test_function_via_inp_yaml_file(monkeypatch, path_tmp):
    args_file = path_tmp / "args.yaml"
    with open(args_file, "w") as fh:
        yaml.dump({"x": 7}, fh)
    received = []

    def fn(x):
        received.append(x)

    monkeypatch.setattr("stepup.core.api.amend", lambda **kw: None)
    args = argparse.Namespace(function="fn", json_inp=None, path_inp=str(args_file))
    _dispatch("s.py", {"fn": fn}, args)
    assert received == [7]


def test_inp_file_unknown_extension(monkeypatch, path_tmp):
    # subs_env_vars() exits cleanly first (calling amend(env=set())),
    # then loadns raises ValueError on the unsupported .txt suffix.
    args_file = path_tmp / "args.txt"
    with open(args_file, "w") as fh:
        fh.write("x=1")
    monkeypatch.setattr("stepup.core.api.amend", lambda **kw: None)
    args = argparse.Namespace(function="fn", json_inp=None, path_inp=str(args_file))
    with pytest.raises(ValueError, match="unsupported"):
        _dispatch("s.py", {"fn": lambda: None}, args)


def test_missing_function():
    args = argparse.Namespace(function="missing", json_inp=None, path_inp=None)
    with pytest.raises(AttributeError, match=r"s\.py"):
        _dispatch("s.py", {}, args)


def test_return_value_ignored():
    def fn():
        return 42

    args = argparse.Namespace(function="fn", json_inp=None, path_inp=None)
    _dispatch("s.py", {"fn": fn}, args)  # must not raise


# --- Signature filtering ---


def test_unexpected_kwargs_raises_error():
    # Custom kwargs not in the function signature must raise TypeError, not be silently dropped.
    def fn(x):
        pass

    args = argparse.Namespace(function="fn", json_inp='{"x": 1, "z": 99}', path_inp=None)
    with pytest.raises(TypeError, match=r"unexpected arguments.*z"):
        _dispatch("s.py", {"fn": fn}, args)


def test_inspection_passes_all_for_var_keyword():
    received = {}

    def fn(**kwargs):
        received.update(kwargs)

    args = argparse.Namespace(function="fn", json_inp='{"x": 1, "z": 99}', path_inp=None)
    _dispatch("s.py", {"fn": fn}, args)
    assert received == {"x": 1, "z": 99}


def test_inp_filtered_when_not_in_sig():
    received = {}

    def fn(x):
        received["x"] = x

    args = argparse.Namespace(function="fn", json_inp='{"x": 1, "inp": ["a.txt"]}', path_inp=None)
    _dispatch("s.py", {"fn": fn}, args)
    assert received == {"x": 1}
    assert "inp" not in received


def test_json_and_inp_mutually_exclusive(monkeypatch, path_tmp):
    args_file = path_tmp / "args.json"
    with open(args_file, "w") as fh:
        json.dump({}, fh)
    monkeypatch.setattr("sys.argv", ["s.py", "fn", '{"x":1}', f"--inp={args_file}"])
    with pytest.raises(SystemExit):
        driver()


# --- _print_list output ---


def test_no_args_shows_list(capsys):
    def fn():
        pass

    _print_list("s.py", {"fn": fn})
    assert "fn" in capsys.readouterr().out


def test_list_prints_functions(capsys):
    def fn_alpha():
        pass

    _print_list("s.py", {"fn_alpha": fn_alpha})
    out = capsys.readouterr().out
    assert "fn_alpha" in out


def test_list_path_is_relative(capsys, tmp_path):
    abs_path = str(tmp_path / "work.py")
    _print_list(abs_path, {"fn": lambda: None})
    out = capsys.readouterr().out
    assert not out.startswith("/")


def test_list_shows_param_names(capsys):
    def fn(a, b):
        pass

    _print_list("s.py", {"fn": fn})
    out = capsys.readouterr().out
    data = json.loads(out.split(" ", 2)[2][1:-2])
    assert "a" in data
    assert "b" in data


def test_list_shows_defaults(capsys):
    def fn(a, b=5):
        pass

    _print_list("s.py", {"fn": fn})
    out = capsys.readouterr().out
    data = json.loads(out.split(" ", 2)[2][1:-2])
    assert data["a"] is None
    assert data["b"] == 5


def test_list_excludes_imports(capsys):
    def local_fn():
        pass

    local_fn.__module__ = "__main__"

    def imported_fn():
        pass

    imported_fn.__module__ = "some.other.module"

    _print_list(
        "work.py", {"__name__": "__main__", "local_fn": local_fn, "imported_fn": imported_fn}
    )
    out = capsys.readouterr().out
    assert "local_fn" in out
    assert "imported_fn" not in out


def test_list_respects_all(capsys):
    def local_fn():
        pass

    local_fn.__module__ = "__main__"

    def imported_fn():
        pass

    imported_fn.__module__ = "some.other.module"

    _print_list(
        "work.py",
        {
            "__name__": "__main__",
            "__all__": ["imported_fn"],
            "local_fn": local_fn,
            "imported_fn": imported_fn,
        },
    )
    out = capsys.readouterr().out
    assert "local_fn" not in out
    assert "imported_fn" in out


# --- Type validation and coercion ---


def test_type_validation_passes():
    def fn(x: int):
        pass

    args = argparse.Namespace(function="fn", json_inp='{"x": 3}', path_inp=None)
    _dispatch("s.py", {"fn": fn}, args)  # must not raise


def test_type_validation_wrong_type():
    # The cattrs JSON preset coerces strings to int, but not lists to int.
    def fn(x: int):
        pass

    args = argparse.Namespace(function="fn", json_inp='{"x": [1, 2]}', path_inp=None)
    with pytest.raises(TypeError, match="x"):
        _dispatch("s.py", {"fn": fn}, args)


def test_type_validation_optional_allows_none():
    def fn(x: str | None):
        pass

    args = argparse.Namespace(function="fn", json_inp='{"x": null}', path_inp=None)
    _dispatch("s.py", {"fn": fn}, args)  # must not raise


def test_type_validation_optional_accepts_value():
    def fn(x: str | None):
        pass

    args = argparse.Namespace(function="fn", json_inp='{"x": "hi"}', path_inp=None)
    _dispatch("s.py", {"fn": fn}, args)  # must not raise


def test_type_validation_list_elements():
    # The cattrs JSON preset coerces int elements to str, but not a bare int to list[str].
    def fn(x: list[str]):
        pass

    args = argparse.Namespace(function="fn", json_inp='{"x": 42}', path_inp=None)
    with pytest.raises(TypeError, match="x"):
        _dispatch("s.py", {"fn": fn}, args)


def test_type_validation_deep_nesting():
    # Inner int element cannot be iterated as list[str]; the nested error is re-raised as TypeError.
    def fn(x: list[list[str]]):
        pass

    args = argparse.Namespace(function="fn", json_inp='{"x": [42]}', path_inp=None)
    with pytest.raises(TypeError, match="x"):
        _dispatch("s.py", {"fn": fn}, args)


def test_type_validation_coerces_dataclass():
    received = []

    def fn(x: _SampleDC):
        received.append(x)

    args = argparse.Namespace(function="fn", json_inp='{"x": {"a": 1}}', path_inp=None)
    _dispatch("s.py", {"fn": fn}, args)
    assert len(received) == 1
    assert isinstance(received[0], _SampleDC)
    assert received[0].a == 1


def test_type_validation_float_accepts_int():
    def fn(x: float):
        pass

    args = argparse.Namespace(function="fn", json_inp='{"x": 3}', path_inp=None)
    _dispatch("s.py", {"fn": fn}, args)  # int is accepted for float annotation


def test_type_validation_unannotated_skipped():
    received = []

    def fn(x):
        received.append(x)

    args = argparse.Namespace(function="fn", json_inp='{"x": [1, 2]}', path_inp=None)
    _dispatch("s.py", {"fn": fn}, args)
    assert received == [[1, 2]]


def test_type_validation_var_keyword():
    # When **kwargs is present, structuring still applies to annotated params.
    received = []

    def fn(x: int, **kwargs):
        received.append((x, kwargs))

    args = argparse.Namespace(function="fn", json_inp='{"x": 3, "y": "hello"}', path_inp=None)
    _dispatch("s.py", {"fn": fn}, args)
    assert len(received) == 1
    x, rest = received[0]
    assert x == 3
    assert rest == {"y": "hello"}


# ===========================================================================
# Section 2: call() API unit tests  (step() and amend() are mocked)
# ===========================================================================


def test_call_creates_one_step(captured):
    call("./s.py", "fn")
    assert len(captured) == 1


def test_explicit_function(captured):
    call("./s.py", "myrun")
    tokens = shlex.split(captured[0]["command"])
    assert tokens[1] == "myrun"


def test_optional_false_planning_false(captured):
    call("./s.py", "fn")
    assert captured[0]["need"] == Need.DEFAULT


def test_optional_true(captured):
    call("./s.py", "fn", optional=True)
    assert captured[0]["need"] == Need.OPTIONAL


def test_planning_true(captured):
    call("./s.py", "fn", planning=True)
    assert captured[0]["need"] == Need.PLAN


def test_optional_and_planning_raises(captured):
    with pytest.raises(ValueError, match="mutually exclusive"):
        call("./s.py", "fn", optional=True, planning=True)
    assert len(captured) == 0


def test_executable_no_separator_raises(captured):
    with pytest.raises(ValueError, match="path separator"):
        call("script.py", "fn")
    assert len(captured) == 0


def test_args_file_json(captured, monkeypatch, path_tmp):
    monkeypatch.chdir(path_tmp)
    call("./s.py", "fn", args_file="foo.json", x=1)
    assert "--inp=foo.json" in captured[0]["command"]
    assert (path_tmp / "foo.json").exists()
    with open(path_tmp / "foo.json") as fh:
        data = json.load(fh)
    assert data["x"] == 1


def test_args_file_yaml(captured, monkeypatch, path_tmp):
    monkeypatch.chdir(path_tmp)
    call("./s.py", "fn", args_file="foo.yaml", x=2)
    assert "--inp=foo.yaml" in captured[0]["command"]
    assert (path_tmp / "foo.yaml").exists()
    with open(path_tmp / "foo.yaml") as fh:
        data = yaml.safe_load(fh)
    assert data["x"] == 2


def test_args_file_unknown_ext(captured, monkeypatch, path_tmp):
    monkeypatch.chdir(path_tmp)
    with pytest.raises(ValueError, match="unsupported"):
        call("./s.py", "fn", args_file="foo.txt")
    assert len(captured) == 0


def test_no_args_file_uses_cli(captured):
    call("./s.py", "fn", x=99)
    tokens = shlex.split(captured[0]["command"])
    data = json.loads(tokens[-1])
    assert data["x"] == 99


def test_inp_absent_in_forwarded_data_when_empty(captured):
    call("./s.py", "fn")
    data = json.loads(shlex.split(captured[0]["command"])[-1])
    assert "inp" not in data


def test_out_absent_in_forwarded_data_when_empty(captured):
    call("./s.py", "fn")
    data = json.loads(shlex.split(captured[0]["command"])[-1])
    assert "out" not in data


def test_inp_in_step_inputs(captured):
    call("./s.py", "fn", inp=["a.txt"])
    assert "a.txt" in captured[0]["inp"]


def test_out_in_step_outputs(captured):
    call("./s.py", "fn", out=["b.txt"])
    assert "b.txt" in captured[0]["out"]


def test_vol_in_step(captured):
    call("./s.py", "fn", vol=["tmp.txt"])
    assert "tmp.txt" in captured[0]["vol"]


def test_resources_in_step(captured):
    call("./s.py", "fn", resources={"cpu": 2})
    assert captured[0]["resources"] == {"cpu": 2}


def test_env_tracked(captured):
    call("./s.py", "fn", env=["MY_VAR"])
    assert "MY_VAR" in captured[0]["env"]


def test_kwargs_serialized_in_command(captured):
    call("./s.py", "fn", x=42)
    data = json.loads(shlex.split(captured[0]["command"])[-1])
    assert data["x"] == 42


def test_json_size_limit(captured):
    big_str = "a" * (200 * 1024)
    with pytest.raises(ValueError, match="128 KiB"):
        call("./s.py", "fn", data=big_str)
    assert len(captured) == 0
