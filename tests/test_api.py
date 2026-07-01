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
"""Tests for stepup.core.api."""

import pathlib

import pytest
from path import Path

from stepup.core.api import (
    _extract_env_overrides,
    _prepare_run_command,
    get_rpc_client,
    getenv,
    loadns,
    shq,
    step,
)
from stepup.core.rpc import DummySyncRPCClient


def noop_amend(*_args, **_kwargs):
    pass


def test_getenv_nonexisting(monkeypatch):
    monkeypatch.setattr("stepup.core.api.amend", noop_amend)
    monkeypatch.delenv("SFDDFHT", raising=False)
    assert getenv("SFDDFHT") is None
    for back in True, False:
        for path in True, False:
            assert len(getenv("SFDDFHT", back=back, path=path, multi=True)) == 0
    with pytest.raises(ValueError):
        getenv("SFDDFHT", back=True)
    with pytest.raises(ValueError):
        getenv("SFDDFHT", path=True)
    with pytest.raises(ValueError):
        getenv("SFDDFHT", path=True, back=True)


@pytest.mark.parametrize("use_default", [True, False])
def test_getenv_single(monkeypatch, use_default):
    monkeypatch.setattr("stepup.core.api.amend", noop_amend)
    monkeypatch.setenv("ROOT", "../")
    monkeypatch.setenv("HERE", "work/")
    if use_default:
        monkeypatch.delenv("SFDDFHT", raising=False)
        default = "sub/asdf"
    else:
        monkeypatch.setenv("SFDDFHT", "sub/asdf")
        default = None
    assert getenv("SFDDFHT", default=default) == "sub/asdf"
    p = getenv("SFDDFHT", default=default, back=True)
    assert isinstance(p, Path)
    assert p == Path("../sub/asdf")
    p = getenv("SFDDFHT", default=default, path=True)
    assert isinstance(p, Path)
    assert p == Path("sub/asdf")
    p = getenv("SFDDFHT", default=default, path=True, back=True)
    assert isinstance(p, Path)
    assert p == Path("../sub/asdf")


@pytest.mark.parametrize("use_default", [True, False])
def test_getenv_default_multi1(monkeypatch, use_default):
    monkeypatch.setattr("stepup.core.api.amend", noop_amend)
    monkeypatch.setenv("ROOT", "../")
    monkeypatch.setenv("HERE", "work/")
    monkeypatch.delenv("SFDDFHT", raising=False)
    if use_default:
        monkeypatch.delenv("SFDDFHT", raising=False)
        default = "sub/asdf"
    else:
        monkeypatch.setenv("SFDDFHT", "sub/asdf")
        default = None
    for path in True, False:
        ps = getenv("SFDDFHT", default=default, path=path, multi=True)
        assert len(ps) == 1
        assert isinstance(ps[0], Path)
        assert ps[0] == Path("sub/asdf")
        ps = getenv("SFDDFHT", default=default, path=path, multi=True, back=True)
        assert len(ps) == 1
        assert isinstance(ps[0], Path)
        assert ps[0] == Path("../sub/asdf")


@pytest.mark.parametrize("use_default", [True, False])
def test_getenv_default_multi3(monkeypatch, use_default):
    monkeypatch.setattr("stepup.core.api.amend", noop_amend)
    monkeypatch.setenv("ROOT", "../")
    monkeypatch.setenv("HERE", "work/")
    if use_default:
        monkeypatch.delenv("SFDDFHT", raising=False)
        default = "sub/asdf:foo:"
    else:
        monkeypatch.setenv("SFDDFHT", "sub/asdf:foo:")
        default = None
    for path in True, False:
        ps = getenv("SFDDFHT", default=default, path=path, multi=True)
        assert len(ps) == 2
        assert isinstance(ps[0], Path)
        assert ps[0] == Path("sub/asdf")
        assert isinstance(ps[1], Path)
        assert ps[1] == Path("foo")
        ps = getenv("SFDDFHT", default=default, path=path, multi=True, back=True)
        assert len(ps) == 2
        assert isinstance(ps[0], Path)
        assert ps[0] == Path("../sub/asdf")
        assert isinstance(ps[1], Path)
        assert ps[1] == Path("../foo")


def test_loadns_py1(path_tmp):
    path_foo = path_tmp / "foo.py"
    with open(path_foo, "w") as fh:
        print("a = 10", file=fh)
    ns = loadns(path_foo)
    assert ns.a == 10


def test_loadns_py2(path_tmp):
    path_foo = path_tmp / "foo.py"
    with open(path_foo, "w") as fh:
        print("a = 10", file=fh)
    path_bar = path_tmp / "bar.py"
    with open(path_bar, "w") as fh:
        print("from foo import a", file=fh)
    ns = loadns(path_bar)
    assert ns.a == 10


def test_loadns_json(path_tmp, monkeypatch):
    path_foo = path_tmp / "foo.json"
    with open(path_foo, "w") as fh:
        print('{"a": 10}', file=fh)
    monkeypatch.setenv("STEPUP_LOADNS_JSON_FOO", "foo")
    ns = loadns(path_tmp / "${STEPUP_LOADNS_JSON_FOO}.json")
    assert ns.a == 10


def test_get_rpc_client_no_socket(monkeypatch):
    monkeypatch.delenv("STEPUP_DIRECTOR_SOCKET", raising=False)
    client = get_rpc_client()
    assert isinstance(client, DummySyncRPCClient)


def test_get_rpc_client_explicit_none(monkeypatch):
    monkeypatch.delenv("STEPUP_DIRECTOR_SOCKET", raising=False)
    client = get_rpc_client(socket=None)
    assert isinstance(client, DummySyncRPCClient)


def test_get_rpc_client_invalid_socket():
    with pytest.raises(RuntimeError, match="director process"):
        get_rpc_client(socket="_invalid_socket_for_director_process_")


def test_getenv_pathlib_default(monkeypatch):
    monkeypatch.setattr("stepup.core.api.amend", noop_amend)
    monkeypatch.delenv("SFDDFHT", raising=False)
    value = getenv("SFDDFHT", default=pathlib.PurePath("sub/asdf"), path=True)
    assert isinstance(value, Path)
    assert value == Path("sub/asdf")


def test_loadns_pathlib(path_tmp):
    path_foo = path_tmp / "foo.json"
    with open(path_foo, "w") as fh:
        print('{"a": 10}', file=fh)
    ns = loadns(pathlib.Path(path_foo))
    assert ns.a == 10


@pytest.mark.parametrize(
    ("command", "env_overrides", "remaining"),
    [
        # No assignments.
        ("./script.py arg", None, "./script.py arg"),
        ("echo hello", None, "echo hello"),
        ("", None, ""),
        # A single assignment.
        ("FOO=bar ./script.py", {"FOO": "bar"}, "./script.py"),
        # Multiple assignments.
        ("A=1 B=2 ./run.sh", {"A": "1", "B": "2"}, "./run.sh"),
        # Quoted value with spaces.
        ('GREETING="hello world" ./show.py', {"GREETING": "hello world"}, "./show.py"),
        ("X='a b' ./show.py", {"X": "a b"}, "./show.py"),
        # Value containing an equals sign.
        ("KEY=a=b ./run.sh", {"KEY": "a=b"}, "./run.sh"),
        # Empty value.
        ("EMPTY= ./run.sh", {"EMPTY": ""}, "./run.sh"),
        # A non-leading assignment is not extracted.
        ("./cmd FOO=bar", None, "./cmd FOO=bar"),
        # The remaining placeholders are preserved verbatim.
        ("FOO=bar ./script.py ${inp} ${out}", {"FOO": "bar"}, "./script.py ${inp} ${out}"),
        # Lowercase command word that is not an assignment.
        ("9NOTVAR=1 ./run.sh", None, "9NOTVAR=1 ./run.sh"),
    ],
)
def test_extract_env_overrides(command, env_overrides, remaining):
    assert _extract_env_overrides(command) == (env_overrides, remaining)


@pytest.mark.parametrize(
    ("command", "exe"),
    [
        # A slash in the assignment value must not be mistaken for a relative executable.
        ("MATPLOTLIBRC=../matplotlibrc python3 -W ignore script.py", None),
        # The real relative executable after the assignment is still detected.
        ("MATPLOTLIBRC=../matplotlibrc ./script.py", "./script.py"),
    ],
)
def test_prepare_run_command_shell_env_assignment_with_slash_value(command, exe):
    # Regression test: with shell=True, a leading `VAR=value` assignment must not be
    # mistaken for a relative executable, even when `value` contains a `/`.
    out_command, out_exe, env_overrides = _prepare_run_command(
        command, shell=True, need_relative_exe=False
    )
    assert out_command == command
    assert out_exe == exe
    assert env_overrides is None


@pytest.mark.parametrize("shell", [True, False])
def test_prepare_run_command_unbalanced_quotes(shell):
    # Regression test: unparsable shell-quoting must raise a clear ValueError,
    # not propagate a bare shlex exception or silently fall back to whitespace-splitting.
    with pytest.raises(ValueError, match="Cannot parse command to detect the executable"):
        _prepare_run_command(
            './script.py --title="Unbalanced', shell=shell, need_relative_exe=False
        )


def test_step_env_overrides_overlap_with_env():
    with pytest.raises(ValueError, match="env dependency and a env_overrides override"):
        step("./script.py", env=["FOO"], env_overrides={"FOO": "bar"})


def test_step_env_overrides_reserved_name():
    with pytest.raises(ValueError, match="set by StepUp cannot be overridden"):
        step("./script.py", env_overrides={"STEPUP_STEP_I": "1"})


def test_shq_single(monkeypatch):
    monkeypatch.setattr("stepup.core.api.amend", noop_amend)
    assert shq("a.txt") == "a.txt"
    assert shq("a b.txt") == "'a b.txt'"


def test_shq_multi(monkeypatch):
    monkeypatch.setattr("stepup.core.api.amend", noop_amend)
    assert shq(["a.txt", "b.txt"]) == "a.txt b.txt"
    assert shq([]) == ""


def test_shq_env_var(monkeypatch):
    monkeypatch.setattr("stepup.core.api.amend", noop_amend)
    monkeypatch.setenv("MYVAR", "sub")
    assert shq("${MYVAR}/a.txt") == "sub/a.txt"
