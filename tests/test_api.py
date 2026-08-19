# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Tests for stepup.core.api."""

import logging
import pathlib
import re

import attrs
import pytest
from path import Path

from stepup.core import api
from stepup.core.api import (
    _prepare_run_command,
    amend,
    copy,
    dumpns,
    get_rpc_client,
    getenv,
    glob,
    hold,
    loadns,
    plan,
    render_jinja,
    run,
    script,
    shq,
    static,
    step,
)
from stepup.core.constants import DIRECTOR_SOCKET_SENTINEL
from stepup.core.exceptions import AmendWhileHoldingError, PathError, StepUpError
from stepup.core.nglob import NamedGlob
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


@pytest.fixture
def no_cached_rpc_client(monkeypatch):
    """Discard the RPC client that other tests may have cached, and the one created here."""
    monkeypatch.delenv("STEPUP_DIRECTOR_SOCKET", raising=False)
    api._get_cached_rpc_client.cache_clear()
    yield
    api._get_cached_rpc_client.cache_clear()


def test_get_rpc_client_no_socket(no_cached_rpc_client):
    client = get_rpc_client()
    assert isinstance(client, DummySyncRPCClient)


def test_get_rpc_client_explicit_none(no_cached_rpc_client):
    client = get_rpc_client(socket=None)
    assert isinstance(client, DummySyncRPCClient)


def test_get_rpc_client_invalid_socket(no_cached_rpc_client):
    with pytest.raises(RuntimeError, match="director process"):
        get_rpc_client(socket=DIRECTOR_SOCKET_SENTINEL)


def test_get_rpc_client_is_created_once(no_cached_rpc_client):
    """The client is created upon first use, so that importing `api` does not connect."""
    assert api._get_cached_rpc_client.cache_info().currsize == 0
    assert get_rpc_client() is get_rpc_client()
    assert api._get_cached_rpc_client.cache_info().currsize == 1


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
    with pytest.raises(ValueError, match="cannot be both an env dependency and an env_overrides"):
        step("./script.py", env=["FOO"], env_overrides={"FOO": "bar"})


def test_step_env_overrides_reserved_name():
    with pytest.raises(ValueError, match="set by StepUp cannot be overridden"):
        step("./script.py", env_overrides={"STEPUP_JOB_I": "1"})


def test_step_negative_duration():
    with pytest.raises(ValueError, match="Invalid duration"):
        step("./script.py", duration=-1.0)


def test_step_nan_duration():
    with pytest.raises(ValueError, match="Invalid duration"):
        step("./script.py", duration=float("nan"))


def test_step_inf_duration():
    with pytest.raises(ValueError, match="Invalid duration"):
        step("./script.py", duration=float("inf"))


def test_step_bool_duration():
    with pytest.raises(ValueError, match="Invalid duration"):
        step("./script.py", duration=True)


def test_step_bool_resource_quantity():
    with pytest.raises(ValueError, match="Invalid quantity"):
        step("./script.py", resources={"gpu": True})


@pytest.fixture
def captured_step_kwargs(monkeypatch):
    """Mock `stepup.core.api.step` and return the kwargs of its last call."""
    calls = []

    def mock_step(command, **kwargs):
        calls.append({"command": command, **kwargs})

    monkeypatch.setattr("stepup.core.api.step", mock_step)
    monkeypatch.setattr("stepup.core.api.amend", noop_amend)
    return calls


def test_run_forwards_duration(captured_step_kwargs):
    run("echo hello", duration=3.5)
    assert captured_step_kwargs[-1]["duration"] == 3.5


def test_plan_forwards_duration(captured_step_kwargs):
    plan("./plan.py", duration=3.5)
    assert captured_step_kwargs[-1]["duration"] == 3.5


def test_copy_forwards_duration(captured_step_kwargs):
    copy("a.txt", "b.txt", duration=3.5)
    assert captured_step_kwargs[-1]["duration"] == 3.5


def test_script_forwards_duration(captured_step_kwargs):
    script("./script.py", duration=3.5)
    assert captured_step_kwargs[-1]["duration"] == 3.5


def test_render_jinja_forwards_duration(captured_step_kwargs):
    render_jinja("template.txt", {"x": 1}, "out.txt", duration=3.5)
    assert captured_step_kwargs[-1]["duration"] == 3.5


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


def test_command_callable_step_out(monkeypatch):
    monkeypatch.setattr("stepup.core.api.amend", noop_amend)
    info = step(lambda out: f"./gen.py {shq(out)}", out=["a.txt", "b.txt"])
    assert info.command == "./gen.py a.txt b.txt"
    assert info.out == ["a.txt", "b.txt"]


def test_command_callable_step_all_params(monkeypatch):
    monkeypatch.setattr("stepup.core.api.amend", noop_amend)
    info = step(
        lambda inp, out, vol: f"./gen.py {shq(inp)} {shq(out)} --log {shq(vol)}",
        inp=["in put.txt"],
        out=["a.txt"],
        vol=["gen.log"],
    )
    assert info.command == "./gen.py 'in put.txt' a.txt --log gen.log"
    assert info.inp == ["in put.txt"]
    assert info.out == ["a.txt"]
    assert info.vol == ["gen.log"]


def test_command_callable_step_no_params(monkeypatch):
    monkeypatch.setattr("stepup.core.api.amend", noop_amend)
    info = step(lambda: "echo hello")
    assert info.command == "echo hello"


def test_command_callable_step_env_var(monkeypatch):
    """One substitution feeds both the command text and the declared paths."""
    monkeypatch.setattr("stepup.core.api.amend", noop_amend)
    monkeypatch.setenv("MYVAR", "sub")
    info = step(lambda out: f"./gen.py {shq(out)}", out=["${MYVAR}/a.txt"])
    assert info.command == "./gen.py sub/a.txt"
    assert info.out == ["sub/a.txt"]


def test_command_callable_run_excludes_exe(captured_step_kwargs):
    """The callable's `inp` never holds the executable that `run()` detects in the command.

    The executable is derived from the command text, which does not exist yet when the
    callable is called. It is also what one wants: the executable already appears as the
    first word of the command the callable writes.
    """
    seen = []

    def make_command(inp):
        seen.append(list(inp))
        return f"./script.py {shq(inp)}"

    run(make_command, inp="data.csv")
    assert seen == [[Path("data.csv")]]
    assert captured_step_kwargs[-1]["command"] == "./script.py data.csv"
    assert captured_step_kwargs[-1]["inp"] == ["./script.py", "data.csv"]


def test_command_callable_plan_exe(captured_step_kwargs):
    seen = []

    def make_command(inp):
        seen.append(list(inp))
        return f"./plan.py {shq(inp)}"

    plan(make_command, inp="config.toml")
    assert seen == [[Path("config.toml")]]
    assert captured_step_kwargs[-1]["command"] == "./plan.py config.toml"
    assert captured_step_kwargs[-1]["inp"] == ["./plan.py", "config.toml"]


def test_command_callable_plan_without_exe_raises(captured_step_kwargs):
    """The error message shows the resolved command, not a `<lambda>` repr."""
    with pytest.raises(PathError, match=re.escape("echo a.txt")):
        plan(lambda out: f"echo {shq(out)}", out="a.txt")
    assert len(captured_step_kwargs) == 0


def test_command_callable_run_env_overrides(captured_step_kwargs):
    run(lambda out: f"OMP_NUM_THREADS=4 ./gen.py {shq(out)}", out="a.txt")
    assert captured_step_kwargs[-1]["command"] == "./gen.py a.txt"
    assert captured_step_kwargs[-1]["env_overrides"] == {"OMP_NUM_THREADS": "4"}
    assert captured_step_kwargs[-1]["inp"] == ["./gen.py"]


def _positional_only_command(inp, /):
    return f"./gen.py {shq(inp)}"


@pytest.mark.parametrize(
    "command",
    [
        lambda foo: "x",
        lambda *args: "x",
        lambda **kwargs: "x",
        _positional_only_command,
    ],
)
def test_command_callable_invalid_signature(monkeypatch, command):
    monkeypatch.setattr("stepup.core.api.amend", noop_amend)
    with pytest.raises(StepUpError):
        step(command, inp="a.txt")


def test_command_callable_generator_inp(captured_step_kwargs):
    """A single-use iterable must not be exhausted by the callable resolution pass."""
    run(lambda inp: f"./gen.py {shq(inp)}", inp=(p for p in ["a.txt", "b.txt"]))
    assert captured_step_kwargs[-1]["command"] == "./gen.py a.txt b.txt"
    assert captured_step_kwargs[-1]["inp"] == ["./gen.py", "a.txt", "b.txt"]


def test_command_callable_empty(monkeypatch):
    monkeypatch.setattr("stepup.core.api.amend", noop_amend)
    with pytest.raises(StepUpError, match="must not be empty"):
        step(lambda: "   ")


def test_amend_raises_while_holding(monkeypatch):
    """The hold() guard is blanket: it fires for any non-trivial `amend()` call made while
    holding, even one that would have resolved instantly and harmlessly. It does not need
    the director/DB, since it is purely a same-process fact tracked by `_HOLD_STATE.holding`.
    """
    monkeypatch.setattr("stepup.core.api._HOLD_STATE.holding", True)
    with pytest.raises(AmendWhileHoldingError):
        amend(inp="some/already_resolved.txt")


def test_amend_noop_does_not_raise_while_holding(monkeypatch):
    """An `amend()` call with nothing to amend must not trip the hold() guard.

    `run.py`/`render_jinja.py` call `amend(inp=get_local_import_paths())` unconditionally
    after a Python step runs, and that list can be empty. If such a no-op call tripped the
    guard, an unrelated held step could fail through no fault of its own code.
    """
    monkeypatch.setattr("stepup.core.api._HOLD_STATE.holding", True)
    amend()


def test_amend_inp_with_out_and_vol_still_raises_while_holding(monkeypatch):
    """A non-empty `inp` still trips the guard even when combined with `out`/`vol`: `inp` is
    the only argument whose producer could plausibly be a step held back by the same
    `hold()` block.
    """
    monkeypatch.setattr("stepup.core.api._HOLD_STATE.holding", True)
    with pytest.raises(AmendWhileHoldingError):
        amend(
            inp="some/already_resolved.txt",
            out="some/new_output.txt",
            vol="some/new_volatile.txt",
        )


def test_amend_env_only_does_not_raise_while_holding(monkeypatch):
    """An `env`-only amend can never depend on a held-back step's output, so it must remain
    allowed inside a `hold()` block. This is what makes `getenv()` inside `hold()` safe.
    """
    monkeypatch.setattr("stepup.core.api._HOLD_STATE.holding", True)
    amend(env="SOME_HOLD_TEST_VAR")


def test_amend_out_only_does_not_raise_while_holding(monkeypatch):
    """An `out`-only amend declares a file the calling step itself produces, so it can never
    depend on a held-back step. This is what makes `dumpns()` inside `hold()` safe.
    """
    monkeypatch.setattr("stepup.core.api._HOLD_STATE.holding", True)
    amend(out="some/new_output.txt")


def test_amend_vol_only_does_not_raise_while_holding(monkeypatch):
    """A `vol`-only amend, like `out`, declares a file the calling step itself produces."""
    monkeypatch.setattr("stepup.core.api._HOLD_STATE.holding", True)
    amend(vol="some/new_volatile.txt")


def test_getenv_does_not_raise_while_holding(monkeypatch):
    """`getenv()` calls `amend(env=...)` internally.

    It must keep working inside a `hold()` block, since it is exactly the kind of call the
    `hold()` docstring's "batch of `run()`/`step()` calls" use case expects to work.
    """
    monkeypatch.setattr("stepup.core.api._HOLD_STATE.holding", True)
    monkeypatch.setenv("SOME_HOLD_TEST_VAR", "1")
    assert getenv("SOME_HOLD_TEST_VAR") == "1"


def test_dumpns_does_not_raise_while_holding(path_tmp, monkeypatch):
    """`dumpns()` calls `amend(out=...)` internally and must keep working while holding."""
    monkeypatch.setattr("stepup.core.api._HOLD_STATE.holding", True)
    path_out = path_tmp / "held.json"
    dumpns(path_out, {"a": 1})
    assert path_out.exists()


class _ReleaseFailsClient(DummySyncRPCClient):
    """A dummy RPC client whose `release_dispatch` call raises, to simulate an RPC failure."""

    def __call__(self, name: str, *args, _rpc_timeout: float | None = None, **kwargs):
        if name == "release_dispatch":
            raise RuntimeError("simulated release() RPC failure")
        return super().__call__(name, *args, _rpc_timeout=_rpc_timeout, **kwargs)


def test_hold_release_success_decrements_holding(monkeypatch):
    """The happy path: `release()` succeeds, so `_HOLD_STATE.holding` returns to its
    pre-block value once the `with hold():` block exits.
    """
    monkeypatch.setattr(api._HOLD_STATE, "holding", 0)
    with hold():
        assert api._HOLD_STATE.holding == 1
    assert api._HOLD_STATE.holding == 0


def test_hold_release_failure_alone_propagates_and_stays_held(monkeypatch):
    """A `release()` RPC failure with no other exception in flight must still raise:
    a release failure on its own is a real error and must not be silently swallowed.

    `_HOLD_STATE.holding` must not be decremented, since the release was never confirmed:
    `amend()`'s guard must stay fail-closed to avoid reintroducing the deadlock risk it
    exists to prevent.
    """
    monkeypatch.setattr(api._HOLD_STATE, "holding", 0)
    client = _ReleaseFailsClient()
    monkeypatch.setattr(api, "_get_cached_rpc_client", lambda: client)
    with pytest.raises(RuntimeError, match="simulated release"), hold():
        pass
    assert api._HOLD_STATE.holding == 1


def test_hold_genuine_exception_not_masked_by_release_failure(monkeypatch, caplog):
    """If code inside `with hold():` raises and `release()` then also fails while unwinding,
    the caller must see the original exception, not the release failure. The release failure
    is logged instead of silently dropped.
    """
    monkeypatch.setattr(api._HOLD_STATE, "holding", 0)
    client = _ReleaseFailsClient()
    monkeypatch.setattr(api, "_get_cached_rpc_client", lambda: client)
    with (
        caplog.at_level(logging.WARNING, logger="stepup.core.api"),
        pytest.raises(ValueError, match="boom"),
        hold(),
    ):
        raise ValueError("boom")
    assert api._HOLD_STATE.holding == 1
    assert any("release" in record.message for record in caplog.records)


def test_check_inp_path_file(path_tmp):
    path_foo = path_tmp / "foo.txt"
    path_foo.write_text("content")
    assert api._check_inp_path(path_foo) is None
    assert api._check_inp_path(path_foo, return_dir=True) is False


def test_check_inp_path_dir_disallowed(path_tmp):
    with pytest.raises(PathError, match="Directory inputs are not supported"):
        api._check_inp_path(path_tmp)


def test_check_inp_path_dir_allowed(path_tmp):
    assert api._check_inp_path(path_tmp, return_dir=True) is True


def test_check_inp_path_missing(path_tmp):
    path_missing = path_tmp / "missing.txt"
    with pytest.raises(PathError, match="Path does not exist"):
        api._check_inp_path(path_missing)
    with pytest.raises(PathError, match="Path does not exist"):
        api._check_inp_path(path_missing, return_dir=True)


def test_check_inp_paths_splits_files_and_dirs(path_tmp):
    path_foo = path_tmp / "foo.txt"
    path_foo.write_text("content")
    path_sub = path_tmp / "sub"
    path_sub.mkdir()
    file_paths, dir_paths = api._check_inp_paths([path_foo, path_sub], allow_dirs=True)
    assert file_paths == [path_foo]
    assert dir_paths == [path_sub]
    with pytest.raises(PathError, match="Directory inputs are not supported"):
        api._check_inp_paths([path_foo, path_sub])


@attrs.define
class _CaptureNglobClient(DummySyncRPCClient):
    """A dummy RPC client that records the arguments of the `register_glob` call."""

    calls: list = attrs.field(factory=list)

    def __call__(self, name: str, *args, _rpc_timeout: float | None = None, **kwargs):
        if name == "register_glob":
            self.calls.append(args)
        return super().__call__(name, *args, _rpc_timeout=_rpc_timeout, **kwargs)


def test_glob_dir_pattern_keeps_trailing_slash(path_tmp, monkeypatch):
    """A directory pattern's trailing separator must survive `subs_env_vars()` + `normpath()`.

    `Path("src/*/").normpath()` strips the trailing slash. If that normalization happens
    before the affixes are captured, `glob()` sends the director a file pattern (`src/*`)
    instead of a directory pattern (`src/*/`), so it silently matches files too.
    """
    monkeypatch.chdir(path_tmp)
    (path_tmp / "src" / "sub").makedirs()
    monkeypatch.setenv("STEPUP_JOB_I", "0")
    client = _CaptureNglobClient()
    monkeypatch.setattr(api, "_get_cached_rpc_client", lambda: client)
    glob("src/*/")
    assert len(client.calls) == 1
    tr_pattern = client.calls[0][1]
    assert tr_pattern == "src/*/"


def test_glob_sends_no_dir_paths(path_tmp, monkeypatch):
    """`glob()`'s RPC payload is a pure query: job_i, pattern, subs, matches -- no dir_paths."""
    monkeypatch.chdir(path_tmp)
    (path_tmp / "src").makedirs()
    (path_tmp / "src" / "a.txt").write_text("content")
    monkeypatch.setenv("STEPUP_JOB_I", "0")
    client = _CaptureNglobClient()
    monkeypatch.setattr(api, "_get_cached_rpc_client", lambda: client)
    glob("src/*.txt")
    assert len(client.calls) == 1
    # job_i, pattern, subs, matches -- 3 arguments after job_i, no dir_paths.
    assert len(client.calls[0]) == 4


@attrs.define
class _CaptureCallOrderClient(DummySyncRPCClient):
    """A dummy RPC client that records the arguments of each RPC call."""

    calls: list = attrs.field(factory=list)

    def __call__(self, name: str, *args, _rpc_timeout: float | None = None, **kwargs):
        self.calls.append((name, args))
        return super().__call__(name, *args, _rpc_timeout=_rpc_timeout, **kwargs)


@pytest.mark.parametrize("order", [("src/", "src/foo.txt"), ("src/foo.txt", "src/")])
def test_static_tree_before_file_regardless_of_argument_order(path_tmp, monkeypatch, order):
    """Within one `static()` call, the tree must reach the director before any file it contains.

    Otherwise the director would see `src/foo.txt` declared before the tree `src/` exists,
    which it rejects, even though the tree was named in the same `static()` call.
    The client only groups and sorts the paths into `tree_paths` and `file_paths`;
    the director is what enforces the ordering, inside a single transaction.
    """
    monkeypatch.chdir(path_tmp)
    (path_tmp / "src").mkdir()
    (path_tmp / "src" / "foo.txt").write_text("content")
    monkeypatch.setenv("STEPUP_JOB_I", "0")
    client = _CaptureCallOrderClient()
    monkeypatch.setattr(api, "_get_cached_rpc_client", lambda: client)
    static(*order)
    assert len(client.calls) == 1
    name, args = client.calls[0]
    assert name == "declare_static"
    _job_i, tree_paths, file_paths, patterns = args
    assert tree_paths == ["src"]
    assert file_paths == ["src/foo.txt"]
    assert patterns == []


@attrs.define
class _CaptureStaticClient(DummySyncRPCClient):
    """A dummy RPC client that records the arguments of the `declare_static` call."""

    calls: list = attrs.field(factory=list)

    def __call__(self, name: str, *args, _rpc_timeout: float | None = None, **kwargs):
        if name == "declare_static":
            self.calls.append(args)
        return super().__call__(name, *args, _rpc_timeout=_rpc_timeout, **kwargs)


def test_static_pattern_splits_files_and_dirs(path_tmp, monkeypatch):
    """A directory match in a `static()` pattern goes to `tree_paths`.

    A file match goes to `file_paths`.
    """
    monkeypatch.chdir(path_tmp)
    (path_tmp / "src" / "sub").makedirs()
    (path_tmp / "src" / "foo.txt").write_text("content")
    monkeypatch.setenv("STEPUP_JOB_I", "0")
    client = _CaptureStaticClient()
    monkeypatch.setattr(api, "_get_cached_rpc_client", lambda: client)
    static("src/*")
    assert len(client.calls) == 1
    _job_i, tree_paths, file_paths, _patterns = client.calls[0]
    assert tree_paths == ["src/sub"]
    assert file_paths == ["src/foo.txt"]


def test_static_pattern_registers_pattern_with_matches(path_tmp, monkeypatch):
    """`static()` registers a pattern together with its matches, not just the matches."""
    monkeypatch.chdir(path_tmp)
    path_tmp.joinpath("src").mkdir()
    (path_tmp / "src" / "a.txt").write_text("content")
    (path_tmp / "src" / "b.txt").write_text("content")
    monkeypatch.setenv("STEPUP_JOB_I", "0")
    client = _CaptureStaticClient()
    monkeypatch.setattr(api, "_get_cached_rpc_client", lambda: client)
    static("src/*.txt")
    assert len(client.calls) == 1
    _job_i, _tree_paths, file_paths, patterns = client.calls[0]
    assert file_paths == ["src/a.txt", "src/b.txt"]
    assert patterns == [("src/*.txt", ["src/a.txt", "src/b.txt"])]
    assert "src/*.txt" not in file_paths


def test_static_dir_pattern_keeps_trailing_slash(path_tmp, monkeypatch):
    """A directory pattern's trailing separator must survive substitution and normalization.

    Mirrors `test_glob_dir_pattern_keeps_trailing_slash` for `static()`:
    both the registered pattern and its matches keep the trailing separator.
    """
    monkeypatch.chdir(path_tmp)
    (path_tmp / "src" / "sub").makedirs()
    monkeypatch.setenv("STEPUP_JOB_I", "0")
    client = _CaptureStaticClient()
    monkeypatch.setattr(api, "_get_cached_rpc_client", lambda: client)
    static("src/*/")
    assert len(client.calls) == 1
    _job_i, tree_paths, _file_paths, patterns = client.calls[0]
    assert tree_paths == ["src/sub"]
    assert patterns == [("src/*/", ["src/sub/"])]


def test_static_empty_pattern_still_registered(path_tmp, monkeypatch):
    """A pattern with zero matches is still registered, so a later run can react to a new match."""
    monkeypatch.chdir(path_tmp)
    monkeypatch.setenv("STEPUP_JOB_I", "0")
    client = _CaptureStaticClient()
    monkeypatch.setattr(api, "_get_cached_rpc_client", lambda: client)
    static("nothing*.txt")
    assert len(client.calls) == 1
    _job_i, tree_paths, file_paths, patterns = client.calls[0]
    assert tree_paths == []
    assert file_paths == []
    assert patterns == [("nothing*.txt", [])]


def test_static_no_arguments_makes_no_call(path_tmp, monkeypatch):
    """`static()` with nothing to declare makes no RPC call at all."""
    monkeypatch.chdir(path_tmp)
    monkeypatch.setenv("STEPUP_JOB_I", "0")
    client = _CaptureStaticClient()
    monkeypatch.setattr(api, "_get_cached_rpc_client", lambda: client)
    static()
    static([])
    assert len(client.calls) == 0


def test_static_named_wildcard_pattern(path_tmp, monkeypatch):
    """A named wildcard in a `static()` pattern is registered verbatim, with no `subs`."""
    monkeypatch.chdir(path_tmp)
    (path_tmp / "f1.txt").write_text("content")
    (path_tmp / "f2.txt").write_text("content")
    monkeypatch.setenv("STEPUP_JOB_I", "0")
    client = _CaptureStaticClient()
    monkeypatch.setattr(api, "_get_cached_rpc_client", lambda: client)
    static("f${*i}.txt")
    assert len(client.calls) == 1
    _job_i, _tree_paths, file_paths, patterns = client.calls[0]
    assert file_paths == ["f1.txt", "f2.txt"]
    assert patterns == [("f${*i}.txt", ["f1.txt", "f2.txt"])]


def test_static_named_wildcard_consistency(path_tmp, monkeypatch):
    """A named wildcard still constrains its occurrences to match the same substring."""
    monkeypatch.chdir(path_tmp)
    path_tmp.joinpath("a").mkdir()
    path_tmp.joinpath("b").mkdir()
    (path_tmp / "a" / "a.txt").write_text("content")
    (path_tmp / "b" / "c.txt").write_text("content")
    monkeypatch.setenv("STEPUP_JOB_I", "0")
    client = _CaptureStaticClient()
    monkeypatch.setattr(api, "_get_cached_rpc_client", lambda: client)
    static("${*n}/${*n}.txt")
    assert len(client.calls) == 1
    _job_i, _tree_paths, file_paths, _patterns = client.calls[0]
    assert file_paths == ["a/a.txt"]


@pytest.mark.parametrize("pattern", ["src/**", "**", "${*name}/**"])
def test_static_recursive_wildcard_rejected(path_tmp, monkeypatch, pattern):
    """A trailing recursive `**` wildcard is rejected before any globbing,

    even over a nonexistent path.
    """
    monkeypatch.chdir(path_tmp)
    monkeypatch.setenv("STEPUP_JOB_I", "0")
    client = _CaptureStaticClient()
    monkeypatch.setattr(api, "_get_cached_rpc_client", lambda: client)
    with pytest.raises(PathError, match="recursive"):
        static(pattern)
    assert len(client.calls) == 0


def test_static_accepts_middle_recursive_wildcard(path_tmp, monkeypatch):
    """A `**` that is not the final path component is accepted and expanded eagerly."""
    monkeypatch.chdir(path_tmp)
    (path_tmp / "src" / "sub").makedirs()
    (path_tmp / "src" / "a.c").write_text("content")
    (path_tmp / "src" / "sub" / "b.c").write_text("content")
    (path_tmp / "src" / "sub" / "c.txt").write_text("content")
    monkeypatch.setenv("STEPUP_JOB_I", "0")
    client = _CaptureStaticClient()
    monkeypatch.setattr(api, "_get_cached_rpc_client", lambda: client)
    result = static("src/**/*.c")
    assert result == ["src/a.c", "src/sub/b.c"]
    assert len(client.calls) == 1
    _job_i, tree_paths, file_paths, patterns = client.calls[0]
    assert tree_paths == []
    assert file_paths == ["src/a.c", "src/sub/b.c"]
    assert patterns == [("src/**/*.c", ["src/a.c", "src/sub/b.c"])]


def test_static_accepts_recursive_named_glob(path_tmp, monkeypatch):
    """A `NamedGlob` whose pattern is recursive is accepted: the scan already happened.

    The `**` rejection only applies to patterns `static()` expands itself.
    """
    monkeypatch.chdir(path_tmp)
    (path_tmp / "src" / "sub").makedirs()
    (path_tmp / "src" / "sub" / "a.txt").write_text("content")
    monkeypatch.setenv("STEPUP_JOB_I", "0")
    client = _CaptureStaticClient()
    monkeypatch.setattr(api, "_get_cached_rpc_client", lambda: client)
    ng = NamedGlob("src/**")
    ng.glob()
    static(ng)
    assert len(client.calls) == 1


def test_static_returns_covered_paths(path_tmp, monkeypatch):
    """The return value lists every path this call covers, trees with a trailing slash."""
    monkeypatch.chdir(path_tmp)
    (path_tmp / "a.txt").write_text("content")
    (path_tmp / "src").mkdir()
    (path_tmp / "data" / "sub").makedirs()
    monkeypatch.setenv("STEPUP_JOB_I", "0")
    client = _CaptureStaticClient()
    monkeypatch.setattr(api, "_get_cached_rpc_client", lambda: client)
    result = static("a.txt", "src/", "data/*")
    assert result == sorted(result)
    assert Path("src/") in result
    assert Path("data/sub/") in result
    assert Path("a.txt") in result


def test_static_accepts_named_glob(path_tmp, monkeypatch):
    """A `NamedGlob` argument contributes its matches without re-registering its pattern."""
    monkeypatch.chdir(path_tmp)
    (path_tmp / "src").mkdir()
    (path_tmp / "src" / "a.txt").write_text("content")
    (path_tmp / "src" / "b.txt").write_text("content")
    monkeypatch.setenv("STEPUP_JOB_I", "0")

    client = _CaptureStaticClient()
    monkeypatch.setattr(api, "_get_cached_rpc_client", lambda: client)
    ng = NamedGlob("src/*.txt")
    ng.glob()
    static(ng)
    assert len(client.calls) == 1
    _job_i, _tree_paths, file_paths, patterns = client.calls[0]
    assert file_paths == ["src/a.txt", "src/b.txt"]
    assert patterns == []

    # Named-wildcard variant: `coerce_paths2` cannot flatten a `NamedGlob`'s iteration,
    # which is exactly the case `_iter_static_args` has to special-case.
    (path_tmp / "f1.txt").write_text("content")
    client2 = _CaptureStaticClient()
    monkeypatch.setattr(api, "_get_cached_rpc_client", lambda: client2)
    ng_named = NamedGlob("f${*i}.txt")
    ng_named.glob()
    static(ng_named)
    assert len(client2.calls) == 1
    _job_i, _tree_paths, file_paths2, patterns2 = client2.calls[0]
    assert file_paths2 == ["f1.txt"]
    assert patterns2 == []

    # Nested form: a `NamedGlob` inside an iterable argument.
    client3 = _CaptureStaticClient()
    monkeypatch.setattr(api, "_get_cached_rpc_client", lambda: client3)
    static([ng])
    assert len(client3.calls) == 1
    _job_i, _tree_paths, file_paths3, patterns3 = client3.calls[0]
    assert file_paths3 == ["src/a.txt", "src/b.txt"]
    assert patterns3 == []


def test_static_env_var_in_pattern(path_tmp, monkeypatch):
    """An environment variable in a `static()` pattern is substituted before registration."""
    monkeypatch.chdir(path_tmp)
    (path_tmp / "src").mkdir()
    (path_tmp / "src" / "a.txt").write_text("content")
    monkeypatch.setenv("DATA", "src")
    monkeypatch.setenv("STEPUP_JOB_I", "0")
    client = _CaptureStaticClient()
    monkeypatch.setattr(api, "_get_cached_rpc_client", lambda: client)
    static("${DATA}/*.txt")
    assert len(client.calls) == 1
    _job_i, _tree_paths, file_paths, patterns = client.calls[0]
    assert file_paths == ["src/a.txt"]
    assert patterns == [("src/*.txt", ["src/a.txt"])]
