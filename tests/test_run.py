# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Unit tests for stepup.core.run"""

import asyncio
import json
import multiprocessing
import os
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import sysconfig
import threading
import time
from types import SimpleNamespace

import pytest

import stepup.core.run as run_mod
from stepup.core.enums import StopLevel
from stepup.core.exceptions import RunError
from stepup.core.outcome import ResourceUsage
from stepup.core.path import Path
from stepup.core.run import (
    COPY_CHUNK_SIZE,
    STOP_SIGNALS,
    WINDOWS_KILL_EXIT_CODE,
    ChildOutcome,
    Run,
    SubprocessWorker,
    ThreadWorker,
    Worker,
    _builtin_copy,
    _communicate,
    _copy_file,
    _decode,
    _detect_python_entrypoint,
    _exec_in_forkserver,
    _executable_compatible_with_current_python,
    _executable_uses_same_python,
    _forkserver_entry,
    _is_builtin_copy,
    _lost_child_outcome,
    describe_stop_level,
    launch_command,
)
from stepup.core.utils import is_process_running

SCRIPTS = Path(sysconfig.get_path("scripts")).name
"""The name of the directory with console scripts in a Python environment."""


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
    # On Windows, a program is only found on `PATH` with one of the extensions of `PATHEXT`.
    interpreter = bindir / ("fakepython.exe" if sys.platform == "win32" else "fakepython")
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

    This covers the measurement plumbing itself (`os.wait4` in `_communicate` plus the
    `perf_counter` bracket around `Popen`), which the pure `ResourceUsage` unit tests in
    test_usage.py cannot exercise because they feed in synthetic `rusage` snapshots.

    The child burns CPU, so that its user time is measurable, and then sleeps.
    On Windows, the CPU time comes from a Job Object, which counts it in clock ticks of 15.6 ms.
    That can put the CPU time of a short child above its wall time,
    unless the wall time holds idle time of more than a tick, which is what the sleep adds.
    """
    step = SimpleNamespace(i=1, label="burn", command_and_workdir=("burn", "."))
    run = Run(step, job_i=1)
    count = 20_000_000
    code = f"import time; print(sum(range({count}))); time.sleep(0.2)"
    command = f"{shlex.quote(sys.executable)} -c {shlex.quote(code)}"

    outcome = await launch_command(
        command, shell=True, env=dict(os.environ), cwd=Path(tmp_path), mp_ctx=None, run=run
    )

    assert outcome.returncode == 0
    assert outcome.stdout.strip() == str(count * (count - 1) // 2)
    # Interpreter startup plus the sum always costs measurable user CPU and wall time.
    assert outcome.usage.utime > 0.0
    assert outcome.usage.stime >= 0.0
    assert outcome.usage.wtime > 0.0
    # The wall time brackets the whole child, so it cannot be shorter than the CPU time
    # of this single-threaded child.
    assert outcome.usage.wtime >= outcome.usage.utime


def test_communicate_without_wait4(monkeypatch: pytest.MonkeyPatch):
    """Without `os.wait4`, a child is still reaped, but its CPU time is reported as zero."""
    monkeypatch.delattr(os, "wait4", raising=False)
    code = "import sys; print(sys.stdin.read()); raise SystemExit(3)"
    proc = subprocess.Popen(
        [sys.executable, "-c", code],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    stdout, stderr, usage = _communicate(proc, b"in", time.perf_counter())
    assert proc.returncode == 3
    assert stdout.strip() == b"in"
    assert stderr == b""
    assert usage.utime == 0.0
    assert usage.stime == 0.0
    assert usage.wtime > 0.0


@pytest.mark.requires_forkserver
def test_lost_child_outcome_signal():
    outcome = _lost_child_outcome(-signal.SIGKILL)
    assert outcome.returncode == -signal.SIGKILL
    assert outcome.stdout == ""
    assert "SIGKILL" in outcome.stderr


def test_lost_child_outcome_exit_code():
    assert _lost_child_outcome(3).returncode == 3
    # A child that vanished cannot be reported as a success.
    assert _lost_child_outcome(0).returncode == 1
    assert _lost_child_outcome(None).returncode == 1


class _FakeChild:
    """A stand-in for a forkserver child process that has already exited.

    Its `sentinel` is the read end of a pipe whose write end is closed,
    so it is immediately readable, just like the sentinel of a dead child.
    """

    def __init__(self, exitcode: int, outcome: ChildOutcome | None, child_conn):
        self.pid = os.getpid()
        self.exitcode = exitcode
        self._outcome = outcome
        self._child_conn = child_conn
        self.sentinel, write_fd = os.pipe()
        os.close(write_fd)

    def start(self):
        if self._outcome is not None:
            self._child_conn.send(self._outcome)

    def join(self):
        os.close(self.sentinel)


class _FakeMPContext:
    """A `multiprocessing` context whose children are `_FakeChild` instances."""

    def __init__(self, exitcode: int, outcome: ChildOutcome | None = None):
        self._exitcode = exitcode
        self._outcome = outcome

    def Pipe(self, duplex: bool = True):  # noqa: N802
        self._parent_conn, self._child_conn = multiprocessing.Pipe(duplex=duplex)
        return self._parent_conn, self._child_conn

    def Process(self, target, args):  # noqa: N802
        return _FakeChild(self._exitcode, self._outcome, self._child_conn)


@pytest.mark.requires_forkserver
async def test_exec_in_forkserver_child_killed():
    """A child killed (e.g. during an aborted build) becomes a failed outcome, not an error."""
    run = Run(SimpleNamespace(i=1, label="killed", command_and_workdir=("killed", ".")), job_i=1)
    mp_ctx = _FakeMPContext(-signal.SIGKILL)

    outcome = await _exec_in_forkserver(mp_ctx, None, (), run)

    assert outcome.returncode == -signal.SIGKILL
    assert "SIGKILL" in outcome.stderr
    assert run.worker is None


@pytest.mark.requires_forkserver
async def test_exec_in_forkserver_outcome_received():
    """A child that does send its outcome has it returned unchanged."""
    run = Run(SimpleNamespace(i=1, label="fine", command_and_workdir=("fine", ".")), job_i=1)
    sent = ChildOutcome(0, "out", "err")
    mp_ctx = _FakeMPContext(0, sent)

    outcome = await _exec_in_forkserver(mp_ctx, None, (), run)

    assert outcome == sent
    assert run.worker is None


def _set_env_bins(monkeypatch, prefix, base_prefix=None):
    if base_prefix is None:
        base_prefix = prefix
    monkeypatch.setattr(sys, "prefix", str(prefix))
    monkeypatch.setattr(sys, "exec_prefix", str(prefix))
    monkeypatch.setattr(sys, "base_prefix", str(base_prefix))
    monkeypatch.setattr(sys, "base_exec_prefix", str(base_prefix))


def test_compatible_fast_path_current_env(monkeypatch, tmp_path):
    bindir = tmp_path / SCRIPTS
    bindir.mkdir()
    exe = bindir / "cmd"
    exe.write_bytes(b"")
    _set_env_bins(monkeypatch, tmp_path)
    assert _executable_compatible_with_current_python(str(exe))


def test_compatible_fast_path_base_env(monkeypatch, tmp_path):
    venv_dir = tmp_path / "venv"
    base_dir = tmp_path / "base"
    (venv_dir / SCRIPTS).mkdir(parents=True)
    (base_dir / SCRIPTS).mkdir(parents=True)
    exe = base_dir / SCRIPTS / "cmd"
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
    assert _detect_python_entrypoint("not_a_console_script_xyz") == (None, "")


def test_detect_entrypoint_compatible(monkeypatch):
    eps = _FakeEntryPoints({"compatible_cmd_xyz": [_FakeEntryPoint("pkg:main")]})
    monkeypatch.setattr(run_mod, "_get_console_script_entry_points", lambda: eps)
    monkeypatch.setattr(shutil, "which", lambda cmd: "/fake/bin/compatible_cmd_xyz")
    monkeypatch.setattr(run_mod, "_executable_compatible_with_current_python", lambda path: True)
    assert _detect_python_entrypoint("compatible_cmd_xyz") == ("pkg:main", "")


def test_detect_entrypoint_incompatible(monkeypatch):
    eps = _FakeEntryPoints({"incompatible_cmd_xyz": [_FakeEntryPoint("pkg:main")]})
    monkeypatch.setattr(run_mod, "_get_console_script_entry_points", lambda: eps)
    monkeypatch.setattr(shutil, "which", lambda cmd: "/fake/bin/incompatible_cmd_xyz")
    monkeypatch.setattr(run_mod, "_executable_compatible_with_current_python", lambda path: False)
    ep_value, warning = _detect_python_entrypoint("incompatible_cmd_xyz")
    assert ep_value is None
    assert "Running it as a subprocess" in warning


def test_detect_entrypoint_incompatible_warns_every_time(monkeypatch):
    eps = _FakeEntryPoints({"incompatible_twice_xyz": [_FakeEntryPoint("pkg:main")]})
    monkeypatch.setattr(run_mod, "_get_console_script_entry_points", lambda: eps)
    monkeypatch.setattr(shutil, "which", lambda cmd: "/fake/bin/incompatible_twice_xyz")
    monkeypatch.setattr(run_mod, "_executable_compatible_with_current_python", lambda path: False)
    first = _detect_python_entrypoint("incompatible_twice_xyz")
    assert first[0] is None
    assert "Running it as a subprocess" in first[1]
    # The second detection is served from the cache and still carries the warning.
    assert _detect_python_entrypoint("incompatible_twice_xyz") == first


def test_detect_entrypoint_caches_lookup(monkeypatch):
    eps = _FakeEntryPoints({"cached_cmd_xyz": [_FakeEntryPoint("pkg:main")]})
    nlookup = 0

    def _count_lookups():
        nonlocal nlookup
        nlookup += 1
        return eps

    monkeypatch.setattr(run_mod, "_get_console_script_entry_points", _count_lookups)
    monkeypatch.setattr(shutil, "which", lambda cmd: "/fake/bin/cached_cmd_xyz")
    monkeypatch.setattr(run_mod, "_executable_compatible_with_current_python", lambda path: True)
    assert _detect_python_entrypoint("cached_cmd_xyz") == ("pkg:main", "")
    assert _detect_python_entrypoint("cached_cmd_xyz") == ("pkg:main", "")
    assert nlookup == 1


def test_detect_entrypoint_broken_installation(monkeypatch):
    eps = _FakeEntryPoints({"broken_cmd_xyz": [_FakeEntryPoint("pkg:main")]})
    monkeypatch.setattr(run_mod, "_get_console_script_entry_points", lambda: eps)
    monkeypatch.setattr(shutil, "which", lambda cmd: None)
    with pytest.raises(RunError):
        _detect_python_entrypoint("broken_cmd_xyz")


#
# Decoding of captured output
#


def test_decode_normalizes_line_endings():
    assert _decode(b"a\r\nb\n\r\nc") == "a\nb\n\nc"
    # A lone carriage return is not a line ending.
    assert _decode(b"progress\rdone\n") == "progress\rdone\n"


def test_decode_drops_invalid_utf8():
    assert _decode("café ".encode() + b"\xff!") == "café !"


def _write_both_line_endings():
    """Write lines with both endings, as text and straight to the file descriptors."""
    sys.stdout.write("text\r\nmixed\n")
    sys.stdout.flush()
    os.write(1, b"fd\r\n")
    os.write(2, b"err\r\n")


@pytest.mark.requires_forkserver
async def test_forkserver_normalizes_line_endings(tmp_path):
    """A step in the forkserver records the same output as it would in a subprocess."""
    outcome = await _exec_in_forkserver(
        multiprocessing.get_context("forkserver"),
        _forkserver_entry,
        ("write", [], dict(os.environ), str(tmp_path), f"{__name__}:_write_both_line_endings"),
        Run(SimpleNamespace(i=1, label="write", command_and_workdir=("write", ".")), job_i=1),
    )
    assert outcome.returncode == 0, outcome.stderr
    assert outcome.stdout == "text\nmixed\nfd\n"
    assert outcome.stderr == "err\n"


#
# Stop levels
#


def test_stop_signals_cover_every_level():
    assert set(STOP_SIGNALS) == set(StopLevel)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
def test_describe_stop_level_posix():
    assert [describe_stop_level(level) for level in StopLevel] == ["SIGINT", "SIGTERM", "SIGKILL"]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
def test_describe_stop_level_windows():
    assert [describe_stop_level(level) for level in StopLevel] == [
        "CTRL_BREAK_EVENT",
        "CTRL_BREAK_EVENT",
        "TerminateJobObject",
    ]


class _RecordingWorker(Worker):
    """A worker that records the signals it would send to a process."""

    def __init__(self):
        super().__init__(job_i=1)
        self.signals = []

    def _describe(self) -> str:
        return "recording worker"

    def _signal(self, sig: int) -> None:
        self.signals.append(sig)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
def test_worker_delivers_stop_levels_as_signals():
    worker = _RecordingWorker()
    for level in StopLevel:
        worker.interrupt(level)
    assert worker.signals == [signal.SIGINT, signal.SIGTERM, signal.SIGKILL]


def test_worker_interrupt_ignores_an_exited_process():
    class _GoneWorker(_RecordingWorker):
        def _signal(self, sig: int) -> None:
            raise ProcessLookupError

    worker = _GoneWorker()
    worker._stop = lambda level: worker._signal(0)
    worker.interrupt(StopLevel.KILL)


def test_worker_without_job_control_is_not_suspended(monkeypatch: pytest.MonkeyPatch):
    """Where the job-control signals do not exist, a suspension leaves the step running."""
    monkeypatch.setattr(run_mod, "suspend_available", lambda: False)
    worker = _RecordingWorker()
    worker.suspend()
    worker.resume()
    assert worker.signals == []


#
# Subprocesses
#


def _make_run(label: str = "step") -> Run:
    return Run(SimpleNamespace(i=1, label=label, command_and_workdir=(label, ".")), job_i=1)


def _python(*args: str) -> str:
    """Format a command that runs the current Python with the given arguments."""
    return shlex.join([sys.executable, *args])


async def test_launch_command_shell_features(tmp_path):
    """A shell command runs in a POSIX shell with the usual tools, on every platform."""
    outcome = await launch_command(
        "echo one; echo two | tr a-z A-Z; echo $((6 * 7)) > answer.txt",
        shell=True,
        env=dict(os.environ),
        cwd=Path(tmp_path),
        mp_ctx=None,
        run=_make_run(),
    )
    assert outcome.returncode == 0
    assert outcome.stdout == "one\nTWO\n"
    assert (tmp_path / "answer.txt").read_text(encoding="utf-8") == "42\n"


async def test_launch_command_shell_receives_the_command_unchanged(tmp_path):
    """Nothing between StepUp and the shell may interpret quotes, wildcards or backslashes."""
    (tmp_path / "a1.txt").write_text("content\n", encoding="utf-8")
    command = (
        r"""printf '%s|' 'a*.txt' "it's" 'back\slash' 'two\\' '@a1.txt' '{p,q}' '"dq"' '~' a*.txt"""
    )
    outcome = await launch_command(
        command, shell=True, env=dict(os.environ), cwd=Path(tmp_path), mp_ctx=None, run=_make_run()
    )
    assert outcome.returncode == 0, outcome.stderr
    assert outcome.stdout == r"""a*.txt|it's|back\slash|two\\|@a1.txt|{p,q}|"dq"|~|a1.txt|"""


async def test_launch_command_shell_not_found(monkeypatch, tmp_path, fresh_shell_cache):
    """A missing shell fails the step with a message, instead of raising in the director."""
    monkeypatch.setenv("STEPUP_SHELL", str(tmp_path / "no-such-shell"))
    outcome = await launch_command(
        "echo hello",
        shell=True,
        env=dict(os.environ),
        cwd=Path(tmp_path),
        mp_ctx=None,
        run=_make_run(),
    )
    assert outcome.returncode == 1
    assert "STEPUP_SHELL" in outcome.stderr


async def test_launch_command_program_not_found(tmp_path):
    run = _make_run()
    outcome = await launch_command(
        "stepup-no-such-program-xyz --flag",
        shell=False,
        env=dict(os.environ),
        cwd=Path(tmp_path),
        mp_ctx=None,
        run=run,
    )
    assert outcome.returncode == 1
    assert "Failed to launch command" in outcome.stderr
    assert run.worker is None


async def test_launch_command_normalizes_line_endings(tmp_path):
    code = r"import sys; sys.stdout.buffer.write(b'a\r\nb\n'); sys.stderr.buffer.write(b'e\r\n')"
    outcome = await launch_command(
        _python("-c", code),
        shell=False,
        env=dict(os.environ),
        cwd=Path(tmp_path),
        mp_ctx=None,
        run=_make_run(),
    )
    assert outcome.returncode == 0
    assert outcome.stdout == "a\nb\n"
    assert outcome.stderr == "e\n"


async def test_launch_command_passes_arguments_unchanged(tmp_path):
    """The arguments of a command without shell reach the program as they were written."""
    args = ["a b", "it's", 'say "hi"', "a*.txt", "back\\slash", "50%", "a&b", ""]
    code = "import json, sys; print(json.dumps(sys.argv[1:]))"
    outcome = await launch_command(
        _python("-c", code, *args),
        shell=False,
        env=dict(os.environ),
        cwd=Path(tmp_path),
        mp_ctx=None,
        run=_make_run(),
    )
    assert outcome.returncode == 0, outcome.stderr
    assert outcome.stdout.strip() == json.dumps(args)


SHEBANG_SCRIPTS = {
    "sh": '#!/bin/sh\necho "sh got $1"\n',
    "sh-env": '#!/usr/bin/env sh\necho "sh got $1"\n',
    "env-split": '#!/usr/bin/env -S bash -e\necho "env got $1"; false; echo not reached\n',
    "python3": "#!/usr/bin/env python3\nimport sys\nprint('python3 got', sys.argv[1])\n",
}
"""Local scripts without the `.py` extension, by the interpreter of their shebang line."""


@pytest.mark.parametrize("kind", list(SHEBANG_SCRIPTS))
async def test_launch_command_local_script_with_shebang(kind: str, tmp_path):
    """A local script runs with the interpreter of its shebang line, on every platform."""
    script = tmp_path / "sub" / "script"
    script.parent.mkdir()
    script.write_text(SHEBANG_SCRIPTS[kind], encoding="utf-8", newline="\n")
    script.chmod(0o755)
    outcome = await launch_command(
        "./script 'one arg'",
        shell=False,
        env=dict(os.environ),
        cwd=Path(tmp_path) / "sub",
        mp_ctx=None,
        run=_make_run(),
    )
    assert outcome.stdout.splitlines()[:1] == [f"{kind.split('-', maxsplit=1)[0]} got one arg"], (
        outcome.stderr
    )
    # The option `-e` of the split shebang line makes the script stop at its failing command.
    assert outcome.returncode == (1 if kind == "env-split" else 0)
    assert "not reached" not in outcome.stdout


async def test_cpu_time_of_a_busy_step(tmp_path):
    """The CPU time of a step that keeps a core busy for half a second is measured."""
    code = (
        "import time\n"
        "start = time.process_time()\n"
        "while time.process_time() - start < 0.5:\n"
        "    pass\n"
    )
    outcome = await launch_command(
        _python("-c", code),
        shell=False,
        env=dict(os.environ),
        cwd=Path(tmp_path),
        mp_ctx=None,
        run=_make_run(),
    )
    assert outcome.returncode == 0, outcome.stderr
    cpu_time = outcome.usage.utime + outcome.usage.stime
    # The lower bound allows for the coarse clock ticks of Windows.
    assert 0.45 < cpu_time < 5.0
    assert outcome.usage.wtime > 0.45


TREE_SCRIPT = """\
import os
import signal
import subprocess
import sys
import time

depth = int(sys.argv[1])
ignore = sys.argv[2] == "ignore"
if ignore:
    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), signal.SIG_IGN)
if depth > 0:
    subprocess.Popen([sys.executable, __file__, str(depth - 1), sys.argv[2]])
with open(f"pid_{depth}.tmp", "w", encoding="utf-8") as fh:
    fh.write(str(os.getpid()))
os.replace(f"pid_{depth}.tmp", f"pid_{depth}.txt")
deadline = time.monotonic() + 60
while time.monotonic() < deadline:
    time.sleep(0.05)
"""
"""A process that starts a copy of itself and then waits, optionally ignoring interrupts.

The wait is a loop of short sleeps,
so that an ignored signal cannot cut one long sleep short.
"""

TREE_TIMEOUT = 10.0
"""Seconds to wait for the process tree to appear or to disappear."""


async def _start_tree(tmp_path, mode: str) -> tuple[Run, asyncio.Task, list[int]]:
    """Launch a step with a grandchild, and return when both processes are running."""
    (tmp_path / "tree.py").write_text(TREE_SCRIPT, encoding="utf-8", newline="\n")
    run = _make_run("tree")
    task = asyncio.create_task(
        launch_command(
            _python("tree.py", "1", mode),
            shell=False,
            env=dict(os.environ),
            cwd=Path(tmp_path),
            mp_ctx=None,
            run=run,
        )
    )
    pid_files = [tmp_path / "pid_1.txt", tmp_path / "pid_0.txt"]
    deadline = time.monotonic() + TREE_TIMEOUT
    while not all(pid_file.exists() for pid_file in pid_files):
        assert not task.done(), task.result()
        assert time.monotonic() < deadline, "The process tree did not start in time."
        await asyncio.sleep(0.05)
    pids = [int(pid_file.read_text(encoding="utf-8")) for pid_file in pid_files]
    assert all(is_process_running(pid) for pid in pids)
    return run, task, pids


async def _wait_gone(pids: list[int]) -> None:
    """Wait until none of the processes exists any longer."""
    deadline = time.monotonic() + TREE_TIMEOUT
    while any(is_process_running(pid) for pid in pids):
        assert time.monotonic() < deadline, "The process tree did not end in time."
        await asyncio.sleep(0.05)


async def test_interrupt_ends_step_and_grandchild(tmp_path):
    """An interrupt reaches the processes that a step started, not only the step itself."""
    run, task, pids = await _start_tree(tmp_path, "obey")
    assert isinstance(run.worker, SubprocessWorker)
    run.worker.interrupt(StopLevel.INTERRUPT)
    outcome = await asyncio.wait_for(task, TREE_TIMEOUT)
    assert outcome.returncode != 0
    assert run.worker is None
    await _wait_gone(pids)


async def test_kill_ends_step_and_grandchild_that_ignore_the_interrupt(tmp_path):
    """Processes that ignore an interrupt keep running, until they are killed as a whole."""
    run, task, pids = await _start_tree(tmp_path, "ignore")
    run.worker.interrupt(StopLevel.INTERRUPT)
    run.worker.interrupt(StopLevel.TERMINATE)
    await asyncio.sleep(0.5)
    assert not task.done()
    assert all(is_process_running(pid) for pid in pids)
    run.worker.interrupt(StopLevel.KILL)
    outcome = await asyncio.wait_for(task, TREE_TIMEOUT)
    expected = WINDOWS_KILL_EXIT_CODE if sys.platform == "win32" else -signal.SIGKILL
    assert outcome.returncode == expected
    await _wait_gone(pids)


def test_interrupt_of_a_step_that_ended_is_harmless():
    """A worker may be interrupted when its process has ended but is not reaped yet."""
    proc = subprocess.Popen(
        [sys.executable, "-c", "pass"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        **run_mod.STEP_ISOLATION,
    )
    worker = SubprocessWorker(proc, run_mod._create_job(proc), job_i=1)
    # The end of the output shows that the process is done, without reaping it,
    # so that its pid cannot have gone to another process, which a signal would then hit.
    assert proc.stdout.read() == b""
    for level in StopLevel:
        worker.interrupt(level)
    proc.wait()
    proc.stdout.close()
    if worker.job is not None:
        worker.job.close()


#
# Built-in copy
#


@pytest.mark.parametrize(
    ("command", "builtin"),
    [
        ("cp -p a.txt b.txt", True),
        ("cp -p 'with space.txt' sub/", True),
        ("cp  -p   a.txt    b.txt", True),
        ("cp a.txt b.txt", False),
        ("cp -p a.txt", False),
        ("cp -p a.txt b.txt c/", False),
        ("cp -pv a.txt b.txt", False),
        ("cp -p -v a.txt", False),
        ("cp -p -- a.txt", False),
        ("cp -a a.txt b.txt", False),
        ("/bin/cp -p a.txt b.txt", False),
        ("cp.exe -p a.txt b.txt", False),
    ],
)
def test_is_builtin_copy(command: str, builtin: bool):
    assert _is_builtin_copy(shlex.split(command)) is builtin


COPY_ATIME_NS = 1_500_000_000_987_654_300
COPY_MTIME_NS = 1_600_000_000_123_456_700
"""Timestamps of a copied file, in multiples of 100 ns, the resolution of Windows."""


def _write_source(path, nbytes: int, mode: int = 0o644) -> bytes:
    """Create a file to copy, with the timestamps above."""
    data = bytes(range(256)) * (nbytes // 256) + bytes(range(nbytes % 256))
    path.write_bytes(data)
    os.utime(path, ns=(COPY_ATIME_NS, COPY_MTIME_NS))
    path.chmod(mode)
    return data


@pytest.mark.parametrize(
    "mode",
    [
        0o444,
        pytest.param(
            0o751, marks=pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
        ),
        pytest.param(
            0o600, marks=pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
        ),
    ],
)
@pytest.mark.parametrize("nbytes", [0, 11, COPY_CHUNK_SIZE, 2 * COPY_CHUNK_SIZE + 17])
def test_copy_file_result_timestamps_and_mode(nbytes: int, mode: int, tmp_path):
    src = tmp_path / "src.bin"
    dst = tmp_path / "dst.bin"
    data = _write_source(src, nbytes, mode)
    assert _copy_file(Path(src), Path(dst), threading.Event())
    dst_stat = dst.stat()
    # Reading the copy below may change its access time.
    assert dst_stat.st_atime_ns == COPY_ATIME_NS
    assert dst_stat.st_mtime_ns == COPY_MTIME_NS
    assert stat.S_IMODE(dst_stat.st_mode) == stat.S_IMODE(src.stat().st_mode)
    if sys.platform != "win32":
        assert stat.S_IMODE(dst_stat.st_mode) == mode
    assert dst.read_bytes() == data
    assert src.read_bytes() == data


def test_copy_file_replaces_the_destination(tmp_path):
    src = tmp_path / "src.bin"
    dst = tmp_path / "dst.bin"
    data = _write_source(src, 100)
    dst.write_bytes(b"x" * 1000)
    assert _copy_file(Path(src), Path(dst), threading.Event())
    assert dst.read_bytes() == data


def test_copy_file_into_a_directory(tmp_path):
    src = tmp_path / "src.bin"
    data = _write_source(src, 100)
    (tmp_path / "sub").mkdir()
    assert _copy_file(Path(src), Path(tmp_path / "sub"), threading.Event())
    assert (tmp_path / "sub" / "src.bin").read_bytes() == data


@pytest.mark.skipif(sys.platform == "win32", reason="needs privileges on Windows")
def test_copy_file_follows_a_symbolic_link(tmp_path):
    src = tmp_path / "src.bin"
    data = _write_source(src, 100)
    link = tmp_path / "link.bin"
    link.symlink_to(src)
    dst = tmp_path / "dst.bin"
    assert _copy_file(Path(link), Path(dst), threading.Event())
    assert not dst.is_symlink()
    assert dst.read_bytes() == data
    assert dst.stat().st_mtime_ns == COPY_MTIME_NS


def test_copy_file_refuses_to_copy_a_file_onto_itself(tmp_path):
    src = tmp_path / "src.bin"
    data = _write_source(src, 100)
    with pytest.raises(OSError, match="same file"):
        _copy_file(Path(src), Path(tmp_path / "." / "src.bin"), threading.Event())
    assert src.read_bytes() == data


def test_builtin_copy_success(tmp_path):
    src = tmp_path / "src.bin"
    data = _write_source(src, 100)
    outcome = _builtin_copy(Path(src), Path(tmp_path / "dst.bin"), threading.Event())
    assert outcome.returncode == 0
    assert outcome.stdout == ""
    assert outcome.stderr == ""
    assert outcome.usage.wtime > 0.0
    assert outcome.usage.utime == 0.0
    assert (tmp_path / "dst.bin").read_bytes() == data


@pytest.mark.parametrize("problem", ["no-source", "source-is-dir", "no-parent"])
def test_builtin_copy_failure(problem: str, tmp_path):
    src = tmp_path / "src.bin"
    dst = tmp_path / "dst.bin"
    if problem == "source-is-dir":
        src.mkdir()
    elif problem == "no-parent":
        _write_source(src, 100)
        dst = tmp_path / "missing" / "dst.bin"
    outcome = _builtin_copy(Path(src), Path(dst), threading.Event())
    assert outcome.returncode == 1
    assert outcome.stdout == ""
    assert outcome.stderr.startswith(f"Failed to copy {Path(src)} to {Path(dst)}: ")
    assert outcome.stderr.endswith("\n")
    assert not dst.exists()


class _CountdownEvent(threading.Event):
    """An event that reports to be set after it was checked a number of times."""

    def __init__(self, nclear: int):
        super().__init__()
        self._nclear = nclear

    def is_set(self) -> bool:
        self._nclear -= 1
        return self._nclear < 0


def test_builtin_copy_interrupted_before_it_starts(tmp_path):
    src = tmp_path / "src.bin"
    _write_source(src, 100)
    dst = tmp_path / "dst.bin"
    cancel_event = threading.Event()
    cancel_event.set()
    outcome = _builtin_copy(Path(src), Path(dst), cancel_event)
    assert outcome.returncode == 1
    assert outcome.stderr == f"Interrupted the copy to {Path(dst)}.\n"
    assert dst.stat().st_size == 0


def test_builtin_copy_interrupted_between_chunks(tmp_path):
    """The cancel event is checked before every chunk, so a large copy stops promptly."""
    src = tmp_path / "src.bin"
    data = _write_source(src, 5 * COPY_CHUNK_SIZE)
    dst = tmp_path / "dst.bin"
    outcome = _builtin_copy(Path(src), Path(dst), _CountdownEvent(2))
    assert outcome.returncode == 1
    assert "Interrupted" in outcome.stderr
    assert dst.read_bytes() == data[: 2 * COPY_CHUNK_SIZE]


def _forbid_subprocesses(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args, **kwargs):
        raise AssertionError("The built-in copy must not start a process.")

    monkeypatch.setattr(run_mod.subprocess, "Popen", forbidden)


async def test_launch_command_copies_without_a_process(monkeypatch, tmp_path):
    """`cp -p SRC DST` without shell is copied by the director, in the directory of the step."""
    workdir = tmp_path / "work"
    (workdir / "out").mkdir(parents=True)
    data = _write_source(workdir / "with space.txt", 100, 0o444)
    _forbid_subprocesses(monkeypatch)
    run = _make_run()
    outcome = await launch_command(
        "cp -p 'with space.txt' out/copy.txt",
        shell=False,
        # Not even `PATH` is needed.
        env={},
        cwd=Path(workdir),
        mp_ctx=None,
        run=run,
    )
    assert outcome == ChildOutcome(0, "", "", outcome.usage)
    assert run.worker is None
    copy = workdir / "out" / "copy.txt"
    assert copy.read_bytes() == data
    assert copy.stat().st_mtime_ns == COPY_MTIME_NS
    assert stat.S_IMODE(copy.stat().st_mode) == stat.S_IMODE(
        (workdir / "with space.txt").stat().st_mode
    )


async def test_launch_command_copy_failure(monkeypatch, tmp_path):
    _forbid_subprocesses(monkeypatch)
    outcome = await launch_command(
        "cp -p missing.txt copy.txt",
        shell=False,
        env={},
        cwd=Path(tmp_path),
        mp_ctx=None,
        run=_make_run(),
    )
    assert outcome.returncode == 1
    assert "Failed to copy" in outcome.stderr
    assert "missing.txt" in outcome.stderr


async def test_launch_command_copy_interrupted(monkeypatch, tmp_path):
    """Interrupting a step that copies cancels the copy, whatever the stop level."""
    started = threading.Event()

    def slow_copy(src, dst, cancel_event):
        started.set()
        assert cancel_event.wait(10)
        return False

    monkeypatch.setattr(run_mod, "_copy_file", slow_copy)
    run = _make_run()
    task = asyncio.create_task(
        launch_command(
            "cp -p a.txt b.txt", shell=False, env={}, cwd=Path(tmp_path), mp_ctx=None, run=run
        )
    )
    await asyncio.to_thread(started.wait, 10)
    assert isinstance(run.worker, ThreadWorker)
    run.worker.interrupt(StopLevel.INTERRUPT)
    outcome = await asyncio.wait_for(task, 10)
    assert outcome.returncode == 1
    assert "Interrupted" in outcome.stderr
    assert run.worker is None


async def test_launch_command_copy_with_shell_is_a_regular_command(monkeypatch, tmp_path):
    """Only the command without shell is built in: a shell step still runs `cp` itself."""

    async def forbidden(*args, **kwargs):
        raise AssertionError("A shell command is not a built-in copy.")

    monkeypatch.setattr(run_mod, "_run_builtin_copy", forbidden)
    data = _write_source(tmp_path / "a.txt", 100)
    outcome = await launch_command(
        "cp -p a.txt b.txt",
        shell=True,
        env=dict(os.environ),
        cwd=Path(tmp_path),
        mp_ctx=None,
        run=_make_run(),
    )
    assert outcome.returncode == 0, outcome.stderr
    assert (tmp_path / "b.txt").read_bytes() == data
