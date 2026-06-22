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

import json
import shlex
from dataclasses import dataclass

import pytest
import yaml

from stepup.core.api import call
from stepup.core.call import _dispatch, _dispatch_plain, _print_list, _registry, callme, driver
from stepup.core.enums import Need
from stepup.core.stepinfo import StepInfo

# ---------------------------------------------------------------------------
# Module-level helper used in type-coercion tests
# ---------------------------------------------------------------------------


@dataclass
class _SampleDC:
    a: int


# ---------------------------------------------------------------------------
# Registry cleanup: prevents state leakage from callme decorator tests
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clean_registry():
    keys_before = set(_registry.keys())
    yield
    for k in list(_registry.keys()):
        if k not in keys_before:
            del _registry[k]


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
# Section 1: _dispatch / callme unit tests  (no director needed)
# ===========================================================================

# --- Basic dispatch ---


def test_function_called_with_kwargs(monkeypatch):
    received = []

    def fn(x, y):
        received.append((x, y))

    monkeypatch.setattr("sys.argv", ["s.py", "fn", '{"x": 1, "y": "hello"}'])
    _dispatch("s.py", {"fn": fn})
    assert received == [(1, "hello")]


def test_no_arguments(monkeypatch):
    # _dispatch always injects inp/out into the forwarded dict;
    # a function with no matching params receives neither.
    called = []

    def fn():
        called.append(True)

    monkeypatch.setattr("sys.argv", ["s.py", "fn", '{"inp": [], "out": []}'])
    _dispatch("s.py", {"fn": fn})
    assert called == [True]


def test_no_args_shows_list(monkeypatch, capsys):
    def fn():
        pass

    monkeypatch.setattr("sys.argv", ["s.py"])
    _dispatch("s.py", {"fn": fn})
    assert "fn" in capsys.readouterr().out


def test_function_via_commandline_json(monkeypatch):
    received = []

    def fn(x):
        received.append(x)

    monkeypatch.setattr("sys.argv", ["s.py", "fn", '{"x": 42}'])
    _dispatch("s.py", {"fn": fn})
    assert received == [42]


def test_function_via_inp_json_file(monkeypatch, path_tmp):
    args_file = path_tmp / "args.json"
    with open(args_file, "w") as fh:
        json.dump({"x": 7}, fh)
    received = []

    def fn(x):
        received.append(x)

    monkeypatch.setattr("stepup.core.api.amend", lambda **kw: None)
    monkeypatch.setattr("sys.argv", ["s.py", "fn", f"--inp={args_file}"])
    _dispatch("s.py", {"fn": fn})
    assert received == [7]


def test_function_via_inp_yaml_file(monkeypatch, path_tmp):
    args_file = path_tmp / "args.yaml"
    with open(args_file, "w") as fh:
        yaml.dump({"x": 7}, fh)
    received = []

    def fn(x):
        received.append(x)

    monkeypatch.setattr("stepup.core.api.amend", lambda **kw: None)
    monkeypatch.setattr("sys.argv", ["s.py", "fn", f"--inp={args_file}"])
    _dispatch("s.py", {"fn": fn})
    assert received == [7]


def test_inp_file_unknown_extension(monkeypatch, path_tmp):
    # subs_env_vars() exits cleanly first (calling amend(env=set())),
    # then loadns raises ValueError on the unsupported .txt suffix.
    args_file = path_tmp / "args.txt"
    with open(args_file, "w") as fh:
        fh.write("x=1")
    monkeypatch.setattr("stepup.core.api.amend", lambda **kw: None)
    monkeypatch.setattr("sys.argv", ["s.py", "fn", f"--inp={args_file}"])
    with pytest.raises(ValueError, match="unsupported"):
        _dispatch("s.py", {"fn": lambda: None})


def test_missing_function(monkeypatch):
    monkeypatch.setattr("sys.argv", ["s.py", "missing"])
    with pytest.raises(AttributeError, match=r"s\.py"):
        _dispatch("s.py", {})


def test_return_value_ignored(monkeypatch):
    def fn():
        return 42

    monkeypatch.setattr("sys.argv", ["s.py", "fn"])
    _dispatch("s.py", {"fn": fn})  # must not raise


# --- Signature filtering ---


def test_unexpected_kwargs_raises_error(monkeypatch):
    # Custom kwargs not in the function signature must raise TypeError, not be silently dropped.
    def fn(x):
        pass

    monkeypatch.setattr("sys.argv", ["s.py", "fn", '{"x": 1, "z": 99}'])
    with pytest.raises(TypeError, match=r"unexpected arguments.*z"):
        _dispatch("s.py", {"fn": fn})


def test_inspection_passes_all_for_var_keyword(monkeypatch):
    received = {}

    def fn(**kwargs):
        received.update(kwargs)

    monkeypatch.setattr("sys.argv", ["s.py", "fn", '{"x": 1, "z": 99}'])
    _dispatch("s.py", {"fn": fn})
    assert received == {"x": 1, "z": 99}


def test_inp_filtered_when_not_in_sig(monkeypatch):
    received = {}

    def fn(x):
        received["x"] = x

    monkeypatch.setattr("sys.argv", ["s.py", "fn", '{"x": 1, "inp": ["a.txt"]}'])
    _dispatch("s.py", {"fn": fn})
    assert received == {"x": 1}
    assert "inp" not in received


def test_json_and_inp_mutually_exclusive(monkeypatch, path_tmp):
    args_file = path_tmp / "args.json"
    with open(args_file, "w") as fh:
        json.dump({}, fh)
    monkeypatch.setattr("sys.argv", ["s.py", "fn", '{"x":1}', f"--inp={args_file}"])
    with pytest.raises(SystemExit):
        _dispatch("s.py", {"fn": lambda: None})


# --- callme decorator ---


def test_callme_registers_function():
    def my_fn():
        pass

    callme(my_fn)
    assert __name__ in _registry
    assert "my_fn" in _registry[__name__]
    assert _registry[__name__]["my_fn"] is my_fn


def test_callme_transparent():
    def my_fn():
        pass

    result = callme(my_fn)
    assert result is my_fn


def test_callme_multiple_functions():
    def fn_a():
        pass

    def fn_b():
        pass

    callme(fn_a)
    callme(fn_b)
    assert "fn_a" in _registry[__name__]
    assert "fn_b" in _registry[__name__]
    assert _registry[__name__]["fn_a"] is fn_a
    assert _registry[__name__]["fn_b"] is fn_b


# --- --list output ---


def test_list_prints_callme_functions(monkeypatch, capsys):
    def fn_alpha():
        pass

    monkeypatch.setattr("sys.argv", ["s.py"])
    _dispatch("s.py", {"fn_alpha": fn_alpha})
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


# --- Type validation and coercion ---


def test_type_validation_passes(monkeypatch):
    def fn(x: int):
        pass

    monkeypatch.setattr("sys.argv", ["s.py", "fn", '{"x": 3}'])
    _dispatch("s.py", {"fn": fn})  # must not raise


def test_type_validation_wrong_type(monkeypatch):
    # The cattrs JSON preset coerces strings to int, but not lists to int.
    def fn(x: int):
        pass

    monkeypatch.setattr("sys.argv", ["s.py", "fn", '{"x": [1, 2]}'])
    with pytest.raises(TypeError, match="x"):
        _dispatch("s.py", {"fn": fn})


def test_type_validation_optional_allows_none(monkeypatch):
    def fn(x: str | None):
        pass

    monkeypatch.setattr("sys.argv", ["s.py", "fn", '{"x": null}'])
    _dispatch("s.py", {"fn": fn})  # must not raise


def test_type_validation_optional_accepts_value(monkeypatch):
    def fn(x: str | None):
        pass

    monkeypatch.setattr("sys.argv", ["s.py", "fn", '{"x": "hi"}'])
    _dispatch("s.py", {"fn": fn})  # must not raise


def test_type_validation_list_elements(monkeypatch):
    # The cattrs JSON preset coerces int elements to str, but not a bare int to list[str].
    def fn(x: list[str]):
        pass

    monkeypatch.setattr("sys.argv", ["s.py", "fn", '{"x": 42}'])
    with pytest.raises(TypeError, match="x"):
        _dispatch("s.py", {"fn": fn})


def test_type_validation_deep_nesting(monkeypatch):
    # Inner int element cannot be iterated as list[str]; the nested error is re-raised as TypeError.
    def fn(x: list[list[str]]):
        pass

    monkeypatch.setattr("sys.argv", ["s.py", "fn", '{"x": [42]}'])
    with pytest.raises(TypeError, match="x"):
        _dispatch("s.py", {"fn": fn})


def test_type_validation_coerces_dataclass(monkeypatch):
    received = []

    def fn(x: _SampleDC):
        received.append(x)

    monkeypatch.setattr("sys.argv", ["s.py", "fn", '{"x": {"a": 1}}'])
    _dispatch("s.py", {"fn": fn})
    assert len(received) == 1
    assert isinstance(received[0], _SampleDC)
    assert received[0].a == 1


def test_type_validation_float_accepts_int(monkeypatch):
    def fn(x: float):
        pass

    monkeypatch.setattr("sys.argv", ["s.py", "fn", '{"x": 3}'])
    _dispatch("s.py", {"fn": fn})  # int is accepted for float annotation


def test_type_validation_unannotated_skipped(monkeypatch):
    received = []

    def fn(x):
        received.append(x)

    monkeypatch.setattr("sys.argv", ["s.py", "fn", '{"x": [1, 2]}'])
    _dispatch("s.py", {"fn": fn})
    assert received == [[1, 2]]


def test_type_validation_var_keyword(monkeypatch):
    # When **kwargs is present, structuring still applies to annotated params.
    received = []

    def fn(x: int, **kwargs):
        received.append((x, kwargs))

    monkeypatch.setattr("sys.argv", ["s.py", "fn", '{"x": 3, "y": "hello"}'])
    _dispatch("s.py", {"fn": fn})
    assert len(received) == 1
    x, rest = received[0]
    assert x == 3
    assert rest == {"y": "hello"}


# --- driver() ---


def test_driver_dispatches_callme_function(monkeypatch):
    """driver() with @callme dispatches via the registry (not caller globals)."""
    received = []

    def my_run(x):
        received.append(x)

    # Populate the __main__ registry directly (simulates @callme in the script).
    _registry["__main__"] = {"my_run": my_run}
    try:
        monkeypatch.setattr("sys.argv", ["work.py", "my_run", '{"x": 7}'])
        driver()
    finally:
        _registry.pop("__main__", None)
    assert received == [7]


def test_driver_dispatches_plain_function(monkeypatch):
    """_dispatch_plain dispatches any callable from a namespace dict."""
    received = []

    def plain_run(x):
        received.append(x)

    monkeypatch.setattr("sys.argv", ["work.py", "plain_run", '{"x": 42}'])
    _dispatch_plain("work.py", {"plain_run": plain_run})
    assert received == [42]


def test_driver_function_not_found_with_callme(monkeypatch):
    """With @callme, wrong name raises AttributeError naming '@callme' function."""

    def existing():
        pass

    _registry["__main__"] = {"existing": existing}
    try:
        monkeypatch.setattr("sys.argv", ["work.py", "missing"])
        with pytest.raises(AttributeError, match="'@callme' function 'missing'"):
            driver()
    finally:
        _registry.pop("__main__", None)


def test_driver_function_not_found_no_callme(monkeypatch):
    """_dispatch_plain raises AttributeError when the name is absent from the namespace."""
    monkeypatch.setattr("sys.argv", ["work.py", "missing"])
    with pytest.raises(AttributeError, match="function 'missing'"):
        _dispatch_plain("work.py", {})


def test_driver_list_mode_with_callme(monkeypatch, capsys):
    """No-arg invocation with @callme prints list of decorated functions."""

    def alpha():
        pass

    _registry["__main__"] = {"alpha": alpha}
    try:
        monkeypatch.setattr("sys.argv", ["work.py"])
        driver()
    finally:
        _registry.pop("__main__", None)
    assert "alpha" in capsys.readouterr().out


def test_driver_list_mode_without_callme(monkeypatch, capsys):
    """No-arg invocation with _dispatch_plain prints public callables from the namespace."""

    def beta():
        pass

    monkeypatch.setattr("sys.argv", ["work.py"])
    _dispatch_plain("work.py", {"beta": beta})
    assert "beta" in capsys.readouterr().out


def test_driver_list_mode_excludes_imports(monkeypatch, capsys):
    """_dispatch_plain omits functions whose __module__ differs from __name__ in the namespace."""

    def local_fn():
        pass

    local_fn.__module__ = "__main__"

    def imported_fn():
        pass

    imported_fn.__module__ = "some.other.module"

    monkeypatch.setattr("sys.argv", ["work.py"])
    _dispatch_plain(
        "work.py", {"__name__": "__main__", "local_fn": local_fn, "imported_fn": imported_fn}
    )
    out = capsys.readouterr().out
    assert "local_fn" in out
    assert "imported_fn" not in out


def test_driver_list_mode_respects_all(monkeypatch, capsys):
    """__all__ overrides module-origin filtering and can expose imported callables."""

    def local_fn():
        pass

    local_fn.__module__ = "__main__"

    def imported_fn():
        pass

    imported_fn.__module__ = "some.other.module"

    monkeypatch.setattr("sys.argv", ["work.py"])
    _dispatch_plain(
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


def test_dispatch_plain_missing(monkeypatch):
    """_dispatch_plain raises AttributeError when the name is not in the namespace."""
    monkeypatch.setattr("sys.argv", ["s.py", "nope"])
    with pytest.raises(AttributeError, match="function 'nope'"):
        _dispatch_plain("s.py", {})


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
