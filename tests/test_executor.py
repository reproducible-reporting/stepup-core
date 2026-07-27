# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Unit tests for stepup.core.executor"""

import asyncio
import sys
from types import SimpleNamespace

import pytest

from stepup.core.executor import Executor, NoOverwriteDict, Run, _executable_uses_same_python


def _make_executor(*, reporter=None) -> Executor:
    """Build an `Executor` with dummy collaborators, sufficient for `report()` tests."""
    return Executor(
        scheduler=None,
        workflow=None,
        db=None,
        reporter=reporter,
        show_perf=False,
        explain_rerun=False,
        live_progress=False,
    )


class _FakeReporter:
    """Records `report()` calls instead of sending them anywhere."""

    def __init__(self):
        self.stopped = []
        self.calls = []

    async def stop_step(self, step_i):
        self.stopped.append(step_i)

    async def __call__(self, action, label, pages):
        self.calls.append((action, label, pages))


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


def _make_detached_run(*, success: bool) -> Run:
    step = SimpleNamespace(i=1, label="one", command_workdir=("echo hi", "."))
    run = Run(step)
    run.success = success
    run.detached = True
    return run


def test_report_marks_detached_step_that_succeeded_as_detached():
    reporter = _FakeReporter()
    executor = _make_executor(reporter=reporter)
    run = _make_detached_run(success=True)

    asyncio.run(executor.report(run))

    assert reporter.stopped == [1]
    action, _label, pages = reporter.calls[0]
    assert action == "DETACHED"
    assert pages[0][0] == "Step detached"


def test_report_marks_detached_step_that_failed_as_detached():
    reporter = _FakeReporter()
    executor = _make_executor(reporter=reporter)
    run = _make_detached_run(success=False)

    asyncio.run(executor.report(run))

    assert reporter.stopped == [1]
    action, _label, pages = reporter.calls[0]
    assert action == "DETACHED"
    assert pages[0][0] == "Step detached"


def test_no_overwrite_dict_allows_insertion_of_new_keys():
    d = NoOverwriteDict()
    d[1] = "a"
    d[2] = "b"
    assert d[1] == "a"
    assert d[2] == "b"
    with pytest.raises(KeyError):
        d[2] = "c"
