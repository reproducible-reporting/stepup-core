# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Tests for stepup.core.api."""

import logging
import pathlib

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
from stepup.core.exceptions import AmendWhileHoldingError, PathError
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
        calls.append(kwargs)

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
    """A dummy RPC client whose `release` call raises, to simulate an RPC failure."""

    def __call__(self, name: str, *args, _rpc_timeout: float | None = None, **kwargs):
        if name == "release":
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
    monkeypatch.setattr(api, "RPC_CLIENT", _ReleaseFailsClient())
    with pytest.raises(RuntimeError, match="simulated release"), hold():
        pass
    assert api._HOLD_STATE.holding == 1


def test_hold_genuine_exception_not_masked_by_release_failure(monkeypatch, caplog):
    """If code inside `with hold():` raises and `release()` then also fails while unwinding,
    the caller must see the original exception, not the release failure. The release failure
    is logged instead of silently dropped.
    """
    monkeypatch.setattr(api._HOLD_STATE, "holding", 0)
    monkeypatch.setattr(api, "RPC_CLIENT", _ReleaseFailsClient())
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
    """A dummy RPC client that records the arguments of the `nglob` call."""

    calls: list = attrs.field(factory=list)

    def __call__(self, name: str, *args, _rpc_timeout: float | None = None, **kwargs):
        if name == "nglob":
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
    monkeypatch.setattr(api, "RPC_CLIENT", client)
    glob("src/*/")
    assert len(client.calls) == 1
    tr_patterns = client.calls[0][1]
    assert tr_patterns == ["src/*/"]


@attrs.define
class _CaptureCallOrderClient(DummySyncRPCClient):
    """A dummy RPC client that records the order in which RPC methods are called."""

    names: list = attrs.field(factory=list)

    def __call__(self, name: str, *args, _rpc_timeout: float | None = None, **kwargs):
        self.names.append(name)
        return super().__call__(name, *args, _rpc_timeout=_rpc_timeout, **kwargs)


@pytest.mark.parametrize("order", [("src/", "src/foo.txt"), ("src/foo.txt", "src/")])
def test_static_tree_before_file_regardless_of_argument_order(path_tmp, monkeypatch, order):
    """Within one `static()` call, the tree must reach the director before any file it contains.

    Otherwise the director would see `src/foo.txt` declared before the tree `src/` exists,
    which it rejects, even though the tree was named in the same `static()` call.
    """
    monkeypatch.chdir(path_tmp)
    (path_tmp / "src").mkdir()
    (path_tmp / "src" / "foo.txt").write_text("content")
    monkeypatch.setenv("STEPUP_JOB_I", "0")
    client = _CaptureCallOrderClient()
    monkeypatch.setattr(api, "RPC_CLIENT", client)
    static(*order)
    assert client.names == ["static_trees", "declare_unconfirmed"]
