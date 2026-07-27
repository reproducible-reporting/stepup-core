# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Unit tests for stepup.core.run"""

import os
import shlex
import shutil
import sys
from types import SimpleNamespace

import pytest
from path import Path

import stepup.core.run as run_mod
from stepup.core.exceptions import RunError
from stepup.core.outcome import ResourceUsage
from stepup.core.run import (
    ChildOutcome,
    Run,
    _detect_python_entrypoint,
    _executable_compatible_with_current_python,
    _executable_uses_same_python,
    launch_command,
)


def test_missing_file(tmp_path):
    assert not _executable_uses_same_python(str(tmp_path / "does_not_exist"))


def test_no_shebang(tmp_path):
    script = tmp_path / "script.py"
    script.write_bytes(b"print('hello')\n")
    assert not _executable_uses_same_python(str(script))


def test_non_ascii_shebang(tmp_path):
    script = tmp_path / "script"
    script.write_bytes(b"#!" + bytes([0xFF, 0xFE]) + b"\n")
    assert not _executable_uses_same_python(str(script))


def test_blank_shebang(tmp_path):
    script = tmp_path / "script"
    script.write_bytes(b"#!   \n")
    assert not _executable_uses_same_python(str(script))


def test_direct_path_match(monkeypatch, tmp_path):
    interpreter = tmp_path / "python3"
    interpreter.write_bytes(b"")
    monkeypatch.setattr(sys, "_base_executable", str(interpreter))
    script = tmp_path / "script"
    script.write_bytes(f"#!{interpreter}\n".encode())
    assert _executable_uses_same_python(str(script))


def test_direct_path_mismatch(monkeypatch, tmp_path):
    interpreter = tmp_path / "python3"
    interpreter.write_bytes(b"")
    other = tmp_path / "python2"
    other.write_bytes(b"")
    monkeypatch.setattr(sys, "_base_executable", str(interpreter))
    script = tmp_path / "script"
    script.write_bytes(f"#!{other}\n".encode())
    assert not _executable_uses_same_python(str(script))


def test_direct_path_through_symlink(monkeypatch, tmp_path):
    # A console_script wrapper installed in a PATH-extended location (e.g. an
    # environment module) commonly points at the base interpreter through a symlink.
    # The shebang and `sys._base_executable` must still compare equal after resolving it.
    interpreter = tmp_path / "python3"
    interpreter.write_bytes(b"")
    link = tmp_path / "python3_link"
    link.symlink_to(interpreter)
    monkeypatch.setattr(sys, "_base_executable", str(interpreter))
    script = tmp_path / "script"
    script.write_bytes(f"#!{link}\n".encode())
    assert _executable_uses_same_python(str(script))


def test_env_form_match(monkeypatch, tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    interpreter = bindir / "fakepython"
    interpreter.write_bytes(b"")
    interpreter.chmod(0o755)
    monkeypatch.setattr(sys, "_base_executable", str(interpreter))
    monkeypatch.setenv("PATH", str(bindir))
    script = tmp_path / "script"
    script.write_bytes(b"#!/usr/bin/env fakepython\n")
    assert _executable_uses_same_python(str(script))


def test_env_form_not_on_path(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "_base_executable", sys.executable)
    monkeypatch.setenv("PATH", str(tmp_path))
    script = tmp_path / "script"
    script.write_bytes(b"#!/usr/bin/env nosuchinterpreter\n")
    assert not _executable_uses_same_python(str(script))


def test_env_form_without_interpreter_argument(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "_base_executable", sys.executable)
    script = tmp_path / "script"
    script.write_bytes(b"#!/usr/bin/env\n")
    assert not _executable_uses_same_python(str(script))


def test_child_outcome_fields():
    usage_ = ResourceUsage(utime=1.0, stime=0.5)
    outcome = ChildOutcome(0, "out", "err", usage=usage_)
    assert outcome.returncode == 0
    assert outcome.stdout == "out"
    assert outcome.stderr == "err"
    assert outcome.usage is usage_


async def test_launch_command_measures_resource_usage(tmp_path):
    """A real child's CPU and wall time end up in the returned `ChildOutcome`.

    This covers the measurement plumbing itself (`os.wait4` in `_communicate_wait4` plus the
    `perf_counter` bracket around `Popen`), which the pure `ResourceUsage` unit tests in
    test_usage.py cannot exercise because they feed in synthetic `rusage` snapshots.
    """
    step = SimpleNamespace(i=1, label="burn", command_workdir=("burn", "."))
    run = Run(step, job_i=1)
    command = f"{shlex.quote(sys.executable)} -c 'print(sum(range(2000000)))'"

    outcome = await launch_command(
        command, subshell=True, env=dict(os.environ), cwd=Path(tmp_path), mp_ctx=None, run=run
    )

    assert outcome.returncode == 0
    assert outcome.stdout.strip() == "1999999000000"
    # Interpreter startup plus the loop always costs measurable user CPU and wall time.
    assert outcome.usage.utime > 0.0
    assert outcome.usage.stime >= 0.0
    assert outcome.usage.wtime > 0.0
    # The wall time brackets the whole child, so it cannot be shorter than the CPU time
    # of this single-threaded child.
    assert outcome.usage.wtime >= outcome.usage.utime


def _set_env_bins(monkeypatch, prefix, base_prefix=None):
    if base_prefix is None:
        base_prefix = prefix
    monkeypatch.setattr(sys, "prefix", str(prefix))
    monkeypatch.setattr(sys, "exec_prefix", str(prefix))
    monkeypatch.setattr(sys, "base_prefix", str(base_prefix))
    monkeypatch.setattr(sys, "base_exec_prefix", str(base_prefix))


def test_compatible_fast_path_current_env(monkeypatch, tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    exe = bindir / "cmd"
    exe.write_bytes(b"")
    _set_env_bins(monkeypatch, tmp_path)
    assert _executable_compatible_with_current_python(str(exe))


def test_compatible_fast_path_base_env(monkeypatch, tmp_path):
    venv_dir = tmp_path / "venv"
    base_dir = tmp_path / "base"
    (venv_dir / "bin").mkdir(parents=True)
    (base_dir / "bin").mkdir(parents=True)
    exe = base_dir / "bin" / "cmd"
    exe.write_bytes(b"")
    _set_env_bins(monkeypatch, venv_dir, base_dir)
    assert _executable_compatible_with_current_python(str(exe))


def test_compatible_slow_path_matching_shebang(monkeypatch, tmp_path):
    other_bin = tmp_path / "other" / "bin"
    other_bin.mkdir(parents=True)
    interpreter = tmp_path / "python3"
    interpreter.write_bytes(b"")
    exe = other_bin / "cmd"
    exe.write_bytes(f"#!{interpreter}\n".encode())
    monkeypatch.setattr(sys, "_base_executable", str(interpreter))
    _set_env_bins(monkeypatch, tmp_path / "venv")
    assert _executable_compatible_with_current_python(str(exe))


def test_compatible_neither_path_nor_shebang(monkeypatch, tmp_path):
    other_bin = tmp_path / "other" / "bin"
    other_bin.mkdir(parents=True)
    exe = other_bin / "cmd"
    exe.write_bytes(b"print('hi')\n")
    monkeypatch.setattr(sys, "_base_executable", sys.executable)
    _set_env_bins(monkeypatch, tmp_path / "venv")
    assert not _executable_compatible_with_current_python(str(exe))


class _FakeEntryPoint:
    def __init__(self, value):
        self.value = value


class _FakeEntryPoints:
    def __init__(self, mapping):
        self._mapping = mapping

    def select(self, name):
        return self._mapping.get(name, [])


def test_detect_entrypoint_not_a_console_script(monkeypatch):
    monkeypatch.setattr(run_mod, "_get_console_script_entry_points", lambda: _FakeEntryPoints({}))
    assert _detect_python_entrypoint("not_a_console_script_xyz") is None


def test_detect_entrypoint_compatible(monkeypatch):
    eps = _FakeEntryPoints({"compatible_cmd_xyz": [_FakeEntryPoint("pkg:main")]})
    monkeypatch.setattr(run_mod, "_get_console_script_entry_points", lambda: eps)
    monkeypatch.setattr(shutil, "which", lambda cmd: "/fake/bin/compatible_cmd_xyz")
    monkeypatch.setattr(run_mod, "_executable_compatible_with_current_python", lambda path: True)
    assert _detect_python_entrypoint("compatible_cmd_xyz") == "pkg:main"


def test_detect_entrypoint_incompatible(monkeypatch, capsys):
    eps = _FakeEntryPoints({"incompatible_cmd_xyz": [_FakeEntryPoint("pkg:main")]})
    monkeypatch.setattr(run_mod, "_get_console_script_entry_points", lambda: eps)
    monkeypatch.setattr(shutil, "which", lambda cmd: "/fake/bin/incompatible_cmd_xyz")
    monkeypatch.setattr(run_mod, "_executable_compatible_with_current_python", lambda path: False)
    assert _detect_python_entrypoint("incompatible_cmd_xyz") is None
    assert "Falling back to direct subprocess execution" in capsys.readouterr().err


def test_detect_entrypoint_broken_installation(monkeypatch):
    eps = _FakeEntryPoints({"broken_cmd_xyz": [_FakeEntryPoint("pkg:main")]})
    monkeypatch.setattr(run_mod, "_get_console_script_entry_points", lambda: eps)
    monkeypatch.setattr(shutil, "which", lambda cmd: None)
    with pytest.raises(RunError):
        _detect_python_entrypoint("broken_cmd_xyz")
