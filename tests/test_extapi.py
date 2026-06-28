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
"""Tests for stepup.core.extapi."""

import contextlib
import hashlib
import shlex
import subprocess
import sys

import pytest

from stepup.core import api, extapi
from stepup.core.extapi import (
    _summarize_binary_stdin,
    filter_dependencies,
    record_subprocess,
    run_subprocess,
)


def test_filter_dependencies(monkeypatch, path_tmp):
    (path_tmp / "project/sub").makedirs()
    monkeypatch.setenv("STEPUP_ROOT", path_tmp / "project")
    abs_paths = [path_tmp / "project/foo", path_tmp / "project/sub/down/bar", "/usr/bin/echo"]
    monkeypatch.chdir(path_tmp / "project/sub")
    assert filter_dependencies(abs_paths) == {"../foo", "down/bar"}


def test_filter_dependencies_external(monkeypatch, path_tmp):
    (path_tmp / "project").makedirs()
    (path_tmp / "pkgs").makedirs()
    monkeypatch.setenv("STEPUP_ROOT", path_tmp / "project")
    monkeypatch.setenv("STEPUP_PATH_FILTER", f"+{path_tmp}/pkgs")
    abs_paths = [path_tmp / "pkgs/helper.py", path_tmp / "other.py"]
    monkeypatch.chdir(path_tmp / "project")
    assert filter_dependencies(abs_paths) == {"../pkgs/helper.py"}


def test_filter_dependencies_external2(monkeypatch, path_tmp):
    (path_tmp / "project").makedirs()
    (path_tmp / "pkgs").makedirs()
    (path_tmp / "templates").makedirs()
    monkeypatch.setenv("STEPUP_ROOT", path_tmp / "project")
    path_filter = f"+{path_tmp}/pkgs/:+{path_tmp}/templates"
    monkeypatch.setenv("STEPUP_PATH_FILTER", path_filter)
    abs_paths = [
        path_tmp / "pkgs/helper.py",
        path_tmp / "other.py",
        path_tmp / "templates/fancy.typ",
    ]
    monkeypatch.chdir(path_tmp / "project")
    assert filter_dependencies(abs_paths) == {"../pkgs/helper.py", "../templates/fancy.typ"}


def test_filter_dependencies_relative1(monkeypatch, path_tmp):
    monkeypatch.setenv("STEPUP_ROOT", path_tmp)
    rel_paths = ["foo.txt", "venv/mod.py", "venv2/bar.py", "egg.py"]
    with contextlib.chdir(path_tmp):
        assert filter_dependencies(rel_paths) == {"foo.txt", "egg.py"}


def test_filter_dependencies_relative2(monkeypatch, path_tmp):
    monkeypatch.setenv("STEPUP_ROOT", path_tmp / "project")
    monkeypatch.setenv("STEPUP_PATH_FILTER", "+../external")
    (path_tmp / "project/sub").makedirs()
    with contextlib.chdir(path_tmp / "project/sub"):
        monkeypatch.setenv("ROOT", path_tmp / "project")
        monkeypatch.setenv("HERE", "sub")
        rel_paths = ["foo", "../../external/bar", "../../egg", "../../other/spam"]
        assert filter_dependencies(rel_paths) == {"foo", "../../external/bar"}


def test_run_subprocess_records_success(monkeypatch):
    """run_subprocess runs the command and records the invocation and return code."""
    recorded = []
    monkeypatch.setattr(
        extapi, "record_subprocess", lambda cmd, rc, **kw: recorded.append((cmd, rc, kw))
    )
    cmd = shlex.join([sys.executable, "-c", ""])
    cp = run_subprocess(cmd)
    assert cp.returncode == 0
    assert cp.stdout == ""
    assert cp.stderr == ""
    assert recorded == [
        (cmd, 0, {"workdir": ".", "env_overrides": None, "shell": False, "stdin": None})
    ]


def test_run_subprocess_check_records_before_raise(monkeypatch):
    """A failing command is recorded before check raises CalledProcessError."""
    recorded = []
    monkeypatch.setattr(extapi, "record_subprocess", lambda cmd, rc, **kw: recorded.append(rc))
    with pytest.raises(subprocess.CalledProcessError):
        run_subprocess(shlex.join([sys.executable, "-c", "import sys; sys.exit(1)"]))
    # The non-zero return code was recorded even though the call raised.
    assert recorded == [1]


def test_run_subprocess_no_check(monkeypatch):
    """With check=False, a non-zero return code does not raise."""
    monkeypatch.setattr(extapi, "record_subprocess", lambda *a, **k: None)
    cp = run_subprocess(shlex.join([sys.executable, "-c", "import sys; sys.exit(2)"]), check=False)
    assert cp.returncode == 2


def test_run_subprocess_env_overlay(monkeypatch):
    """env_overrides is an overlay merged over os.environ; only the overlay is recorded."""
    recorded = []
    monkeypatch.setattr(
        extapi, "record_subprocess", lambda cmd, rc, **kw: recorded.append(kw["env_overrides"])
    )
    monkeypatch.setenv("PRE_EXISTING", "yes")
    cmd = shlex.join(
        [
            "OVERLAY=added",
            sys.executable,
            "-c",
            "import os; print(os.environ.get('OVERLAY'), os.environ.get('PRE_EXISTING'))",
        ]
    )
    cp = run_subprocess(cmd)
    out = cp.stdout
    # The overlay variable and a pre-existing variable are both visible to the subprocess.
    assert out.split() == ["added", "yes"]
    # Only the overlay (not the full resolved environment) is handed to record_subprocess.
    assert recorded == [{"OVERLAY": "added"}]


def test_record_subprocess_no_director(monkeypatch):
    """Outside a running step (dummy client, step_i == -1), record_subprocess is a no-op."""
    calls = []

    class FakeCall:
        @staticmethod
        def record_subprocess(*args):
            calls.append(args)

    class FakeClient:
        call = FakeCall()

    monkeypatch.setattr(api, "_get_step_i", lambda: -1)
    monkeypatch.setattr(api, "RPC_CLIENT", FakeClient())
    record_subprocess("echo hi", 0)
    assert calls == []


def test_run_subprocess_no_director_still_runs(monkeypatch):
    """run_subprocess executes the subprocess even when there is no director to record to."""
    monkeypatch.setattr(api, "_get_step_i", lambda: -1)
    cp = run_subprocess(shlex.join([sys.executable, "-c", ""]))
    assert cp.returncode == 0


def test_run_subprocess_shell_true(monkeypatch):
    """shell=True passes cmd to the system shell and records shell=True."""
    recorded = []
    monkeypatch.setattr(
        extapi, "record_subprocess", lambda cmd, rc, **kw: recorded.append((cmd, rc, kw))
    )
    cp = run_subprocess("echo hello", shell=True)
    assert cp.returncode == 0
    assert recorded == [
        ("echo hello", 0, {"workdir": ".", "env_overrides": None, "shell": True, "stdin": None})
    ]


def test_run_subprocess_shell_false_splits(monkeypatch):
    """shell=False splits cmd with shlex so a quoted argument with spaces arrives intact."""
    monkeypatch.setattr(extapi, "record_subprocess", lambda *a, **k: None)
    # The argument "hello world" contains a space; shlex.split must keep it as one argv element.
    cmd = shlex.join([sys.executable, "-c", "import sys; print(sys.argv[1])", "hello world"])
    cp = run_subprocess(cmd, shell=False)
    assert cp.returncode == 0
    assert cp.stdout.strip() == "hello world"


def test_run_subprocess_stdin(monkeypatch):
    """stdin is fed to the subprocess and forwarded to record_subprocess."""
    recorded = []
    monkeypatch.setattr(
        extapi, "record_subprocess", lambda cmd, rc, **kw: recorded.append(kw["stdin"])
    )
    cmd = shlex.join([sys.executable, "-c", "import sys; sys.stdout.write(sys.stdin.read())"])
    cp = run_subprocess(cmd, stdin="hello stdin")
    assert cp.stdout == "hello stdin"
    assert recorded == ["hello stdin"]


def test_run_subprocess_stdin_bytes(monkeypatch):
    """Binary stdin is passed verbatim to the subprocess and forwarded raw to record_subprocess."""
    recorded = []
    monkeypatch.setattr(
        extapi, "record_subprocess", lambda cmd, rc, **kw: recorded.append(kw["stdin"])
    )
    cmd = shlex.join(
        [sys.executable, "-c", "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())"]
    )
    cp = run_subprocess(cmd, stdin=b"\x00\x01\x02hello")
    assert cp.stdout == b"\x00\x01\x02hello"
    assert recorded == [b"\x00\x01\x02hello"]


def test_run_subprocess_check_prints_stdout_stderr(monkeypatch, capsys):
    """On failure, stdout and stderr from the subprocess are forwarded before raising."""
    monkeypatch.setattr(extapi, "record_subprocess", lambda *a, **k: None)
    cmd = shlex.join(
        [
            sys.executable,
            "-c",
            "import sys; print('out line'); print('err line', file=sys.stderr); sys.exit(3)",
        ]
    )
    with pytest.raises(subprocess.CalledProcessError):
        run_subprocess(cmd)
    captured = capsys.readouterr()
    assert "out line" in captured.out
    assert "err line" in captured.err


def test_run_subprocess_check_prints_stdout_stderr_binary(monkeypatch, capsys):
    """In binary mode, stdout and stderr are decoded before being forwarded."""
    monkeypatch.setattr(extapi, "record_subprocess", lambda *a, **k: None)
    cmd = shlex.join(
        [
            sys.executable,
            "-c",
            "import sys; print('out line'); print('err line', file=sys.stderr); sys.exit(3)",
        ]
    )
    with pytest.raises(subprocess.CalledProcessError):
        run_subprocess(cmd, stdin=b"", text=False)
    captured = capsys.readouterr()
    assert "out line" in captured.out
    assert "err line" in captured.err


def test_run_subprocess_check_no_output_no_print(monkeypatch, capsys):
    """When the failing subprocess produces no output, nothing is written to stdout/stderr."""
    monkeypatch.setattr(extapi, "record_subprocess", lambda *a, **k: None)
    cmd = shlex.join([sys.executable, "-c", "import sys; sys.exit(1)"])
    with pytest.raises(subprocess.CalledProcessError):
        run_subprocess(cmd)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_run_subprocess_stdin_bytes_text_true_raises():
    """Passing bytes stdin with text=True raises ValueError."""
    with pytest.raises(ValueError, match="text=True"):
        run_subprocess(shlex.join([sys.executable, "-c", ""]), stdin=b"data", text=True)


def test_run_subprocess_stdin_str_text_false_raises():
    """Passing str stdin with text=False raises ValueError."""
    with pytest.raises(ValueError, match="text=False"):
        run_subprocess(shlex.join([sys.executable, "-c", ""]), stdin="data", text=False)


def test_summarize_binary_stdin():
    """_summarize_binary_stdin passes through None and str, and summarizes bytes."""
    assert _summarize_binary_stdin(None) is None
    s = "hello"
    assert _summarize_binary_stdin(s) is s
    data = b"\x00\x01\x02hi"
    result = _summarize_binary_stdin(data)
    expected_digest = hashlib.sha256(data).hexdigest()[:16]
    assert result == f"<{len(data)} bytes of binary stdin, sha256={expected_digest}>"
    assert result.startswith(f"<{len(data)} bytes of binary stdin, sha256=")
    assert result.endswith(">")
