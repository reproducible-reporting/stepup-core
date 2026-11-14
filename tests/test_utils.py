# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Tests for stepup.core.utils."""

import io
import os
import subprocess
import sys

import pytest
from core_common import create_socket_path
from rich.console import Console

from stepup.core.path import Path
from stepup.core.popen import _resolve_interpreter, popen_args
from stepup.core.utils import (
    escape_control_chars,
    extract_env_overrides,
    format_subprocess,
    is_process_running,
    is_terminal,
    merge_resources,
    parse_resources,
    query_director_log,
    rich_legacy_windows,
    scan_director_log,
    write_utf8_unless_terminal,
)


@pytest.mark.parametrize(
    ("s", "expected"),
    [
        ("cpu:4,gpu:1,memgb:16", {"cpu": 4, "gpu": 1, "memgb": 16}),
        ("cpu:2", {"cpu": 2}),
        ("cpu:0", {"cpu": 0}),
        ("cpu:", {"cpu": 1}),
        ("cpu", {"cpu": 1}),
        ("  cpu : 4 , gpu ", {"cpu": 4, "gpu": 1}),
        ("", {}),
        (",", {}),
        (",,,", {}),
    ],
)
def test_parse_resources(s, expected):
    assert parse_resources(s) == expected


@pytest.mark.parametrize(
    "s",
    [
        "cpu:-1",
        ":1",
        "  :2",
    ],
)
def test_parse_resources_invalid(s):
    with pytest.raises(ValueError):
        parse_resources(s)


@pytest.mark.parametrize(
    ("base", "override", "expected"),
    [
        # Basic merge: override adds a new key
        ("cpu:4", "gpu:1", "cpu:4,gpu:1"),
        # Override replaces an existing key
        ("cpu:4,gpu:1", "cpu:8", "cpu:8,gpu:1"),
        # Empty base: result is just the override
        ("", "cpu:4", "cpu:4"),
        # Empty override: result is just the base
        ("cpu:4", "", "cpu:4"),
        # Both empty: result is empty string
        ("", "", ""),
        # Override with multiple keys, some new and some replacing
        ("cpu:4,gpu:1,memgb:16", "gpu:2,memgb:32", "cpu:4,gpu:2,memgb:32"),
        # Value defaults to 1 when omitted in override
        ("cpu:4", "gpu", "cpu:4,gpu:1"),
        # Value defaults to 1 when omitted in base
        ("gpu", "cpu:4", "gpu:1,cpu:4"),
        # Override with zero value is valid
        ("cpu:4,gpu:1", "gpu:0", "cpu:4,gpu:0"),
        # Whitespace is stripped
        ("cpu : 4", " gpu : 1 ", "cpu:4,gpu:1"),
        # None base: result is just the override
        (None, "gpu:1", "gpu:1"),
        # None override: result is just the base
        ("cpu:4", None, "cpu:4"),
        # Both None: result is empty string
        (None, None, ""),
    ],
)
def test_merge_resources(base: str | None, override: str | None, expected: str) -> None:
    assert merge_resources(base, override) == expected


@pytest.mark.parametrize(
    "command",
    [
        "echo hello",
        'echo "Monday frown\nCoffee smile" > story.txt',
        pytest.param(
            "printf 'a\tb\r\n'",
            marks=pytest.mark.skipif(
                sys.platform == "win32",
                reason="The bash of Git for Windows drops carriage returns from a script.",
            ),
        ),
        "a'b'c=1; echo done",
        "echo '\x01\x02'",
    ],
)
def test_escape_control_chars_roundtrip(command, path_tmp):
    escaped = escape_control_chars(command)
    assert "\n" not in escaped
    # The `$'...'` quoting is a feature of bash, which the shell of a step need not be.
    bash = _resolve_interpreter("bash", os.environ, path_tmp)
    original, reproduced = (
        subprocess.run(
            popen_args([bash, "-c", script], shell=False, env=os.environ, cwd=path_tmp),
            capture_output=True,
            check=False,
            cwd=path_tmp,
        )
        for script in (command, escaped)
    )
    assert reproduced.stdout == original.stdout
    assert reproduced.returncode == original.returncode


def test_escape_control_chars_no_control_chars():
    command = 'echo "hello" > out.txt'
    assert escape_control_chars(command) == command


def test_format_subprocess_escapes_embedded_newline():
    line = format_subprocess("echo a\nb", ".", returncode=0, shell=True)
    assert "\n" not in line


def test_is_process_running_self():
    """This very process is trivially running."""
    assert is_process_running(os.getpid())


def test_is_process_running_child():
    """A child is running until it exits, also while its parent still holds on to it.

    On Windows, the `Popen` object keeps a handle to the child after `wait()`,
    so the process object of an exited child still exists there.
    """
    with subprocess.Popen([sys.executable, "-c", "input()"], stdin=subprocess.PIPE) as proc:
        assert is_process_running(proc.pid)
        proc.communicate(b"\n")
        assert not is_process_running(proc.pid)


def test_is_process_running_permission_error(monkeypatch: pytest.MonkeyPatch):
    """A pid owned by another user cannot be signaled, so it is assumed alive."""
    monkeypatch.setattr("stepup.core.utils.sys.platform", "linux")

    def raise_permission_error(pid: int, sig: int) -> None:
        raise PermissionError

    monkeypatch.setattr(os, "kill", raise_permission_error)
    assert is_process_running(12345)


def test_is_process_running_no_such_process(monkeypatch: pytest.MonkeyPatch):
    """A pid that no longer exists must not keep the next build from starting."""
    monkeypatch.setattr("stepup.core.utils.sys.platform", "linux")

    def raise_process_lookup_error(pid: int, sig: int) -> None:
        raise ProcessLookupError

    monkeypatch.setattr(os, "kill", raise_process_lookup_error)
    assert not is_process_running(12345)


def test_query_director_log_missing_file(path_tmp: Path):
    socket_path, pid, message = query_director_log(path_tmp / "director.log")
    assert socket_path is None
    assert pid is None
    assert "could not be read" in message


def test_query_director_log_unopenable(path_tmp: Path):
    """A log that exists but cannot be opened must not raise, e.g. when it is a directory.

    This stands in for the race that motivated opening the file without testing first:
    a director starting up in parallel wipes `.stepup/` and can remove the log
    in between an existence test and the `open` call.
    """
    path_log = path_tmp / "director.log"
    path_log.makedirs_p()
    socket_path, pid, message = query_director_log(path_log)
    assert socket_path is None
    assert pid is None
    assert "could not be read" in message


def test_query_director_log_live_socket(path_tmp: Path, dir_sockets: Path, rpc_transport: str):
    socket_path = dir_sockets / "director"
    create_socket_path(socket_path, rpc_transport)
    path_log = path_tmp / "director.log"
    path_log.write_text(f"SOCKET {socket_path}\nPID 12345\nLOG_LEVEL INFO\n", encoding="utf-8")
    assert query_director_log(path_log) == (socket_path, 12345, "")


def test_query_director_log_stale_socket(path_tmp: Path):
    path_log = path_tmp / "director.log"
    path_log.write_text(
        f"SOCKET {path_tmp / 'director'}\nPID 12345\nLOG_LEVEL INFO\n", encoding="utf-8"
    )
    socket_path, pid, message = query_director_log(path_log)
    assert socket_path is None
    assert pid == 12345
    assert "does not exist" in message


def test_query_director_log_socket_path_with_space(
    path_tmp: Path, dir_sockets: Path, rpc_transport: str
):
    """A socket path with a space in it must not be truncated."""
    socket_path = dir_sockets / "with space" / "director"
    socket_path.parent.makedirs_p()
    create_socket_path(socket_path, rpc_transport)
    path_log = path_tmp / "director.log"
    path_log.write_text(f"SOCKET {socket_path}\nPID 12345\n", encoding="utf-8")
    assert query_director_log(path_log)[0] == socket_path


@pytest.mark.parametrize("content", ["", "\n", "Traceback (most recent call last):\n"])
def test_query_director_log_without_socket_line(path_tmp: Path, content: str):
    """A director that crashed before advertising its socket must not confuse the parsing."""
    path_log = path_tmp / "director.log"
    path_log.write_text(content, encoding="utf-8")
    socket_path, pid, message = query_director_log(path_log)
    assert socket_path is None
    assert pid is None
    assert "does not start with SOCKET line" in message


def test_scan_director_log_missing_file(path_tmp: Path):
    """A build that never started a director leaves no log to complain about."""
    assert scan_director_log(path_tmp / "director.log") == []


def test_scan_director_log_unopenable(path_tmp: Path):
    """A log that exists but cannot be opened yields no findings instead of raising."""
    path_log = path_tmp / "director.log"
    path_log.makedirs_p()
    assert scan_director_log(path_log) == []


def test_scan_director_log_clean(path_tmp: Path):
    path_log = path_tmp / "director.log"
    path_log.write_text(
        "SOCKET /tmp/stepup-abcd1234/director\nPID 12345\nLOG_LEVEL DEBUG\n", encoding="utf-8"
    )
    assert scan_director_log(path_log) == []


def test_scan_director_log_order_and_labels(path_tmp: Path):
    """All findings are collected, in the order of the log."""
    path_log = path_tmp / "director.log"
    path_log.write_text(
        "2026-07-27 12:34:56     ERROR                   asyncio  ::  "
        "Task exception was never retrieved\n"
        "future: <Task finished coro=<Watcher.loop() done>>\n"
        "2026-07-27 12:34:57     ERROR      stepup.core.sqlite3  ::  Database is locked\n"
        "sys:1: RuntimeWarning: coroutine 'Watcher.loop' was never awaited\n",
        encoding="utf-8",
    )
    assert scan_director_log(path_log) == [
        "Unretrieved exception: 2026-07-27 12:34:56     ERROR                   asyncio  ::  "
        "Task exception was never retrieved",
        "Logged error: 2026-07-27 12:34:57     ERROR      stepup.core.sqlite3  ::  "
        "Database is locked",
        "Unawaited coroutine: sys:1: RuntimeWarning: coroutine 'Watcher.loop' was never awaited",
    ]


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
    assert extract_env_overrides(command) == (env_overrides, remaining)


def test_is_terminal_without_stream():
    """A process may have no standard streams at all, e.g. under `pythonw.exe`."""
    assert not is_terminal(None)


def test_is_terminal_in_memory_stream():
    assert not is_terminal(io.StringIO())


def test_is_terminal_null_device():
    """The null device is no terminal, although `isatty()` is true for it on Windows."""
    with open(os.devnull, encoding="utf-8") as stream:
        assert not is_terminal(stream)


def test_is_terminal_file(path_tmp: Path):
    with open(path_tmp / "out.txt", "w", encoding="utf-8") as stream:
        assert not is_terminal(stream)


@pytest.mark.requires_termios
def test_is_terminal_pty():
    primary_fd, secondary_fd = os.openpty()
    try:
        with os.fdopen(secondary_fd, "w", encoding="utf-8") as stream:
            assert is_terminal(stream)
    finally:
        os.close(primary_fd)


def test_rich_legacy_windows_file(path_tmp: Path, monkeypatch: pytest.MonkeyPatch):
    """A rule written to a file is as wide as `COLUMNS` says, on every platform."""
    monkeypatch.setenv("COLUMNS", "60")
    path_out = path_tmp / "out.txt"
    with open(path_out, "w", encoding="utf-8", newline="\n") as stream:
        assert rich_legacy_windows(stream) is False
        Console(file=stream, legacy_windows=rich_legacy_windows(stream)).rule()
    assert path_out.read_text(encoding="utf-8") == "─" * 60 + "\n"


@pytest.mark.requires_termios
def test_rich_legacy_windows_pty():
    primary_fd, secondary_fd = os.openpty()
    try:
        with os.fdopen(secondary_fd, "w", encoding="utf-8") as stream:
            assert rich_legacy_windows(stream) is None
    finally:
        os.close(primary_fd)


def _legacy_stream(errors: str) -> io.TextIOWrapper:
    """Create a text stream as Python sets it up for redirected output on a legacy code page."""
    return io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors=errors)


def test_write_utf8_unless_terminal_redirected(monkeypatch: pytest.MonkeyPatch):
    stdout = _legacy_stream("strict")
    stderr = _legacy_stream("backslashreplace")
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)
    write_utf8_unless_terminal()
    for stream in stdout, stderr:
        # The vertical bar of the reporter has no place in the code page.
        stream.write("DIRECTOR \u2502 ok\n")
        stream.flush()
        assert stream.buffer.getvalue() == "DIRECTOR \u2502 ok\n".encode()
    assert stdout.errors == "strict"
    assert stderr.errors == "backslashreplace"


def test_write_utf8_unless_terminal_leaves_a_terminal_alone(monkeypatch: pytest.MonkeyPatch):
    stdout = _legacy_stream("strict")
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stdout)
    monkeypatch.setattr("stepup.core.utils.is_terminal", lambda stream: True)
    write_utf8_unless_terminal()
    assert stdout.encoding == "cp1252"


def test_write_utf8_unless_terminal_leaves_other_streams_alone(monkeypatch: pytest.MonkeyPatch):
    """A replaced or missing standard stream cannot be reconfigured, and needs no encoding."""
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    monkeypatch.setattr(sys, "stderr", None)
    write_utf8_unless_terminal()
