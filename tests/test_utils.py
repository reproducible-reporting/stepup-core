# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Tests for stepup.core.utils."""

import os
import subprocess

import pytest
from path import Path

from stepup.core.utils import (
    escape_command_display,
    extract_env_overrides,
    format_subprocess,
    is_process_running,
    merge_resources,
    parse_resources,
    query_director_log,
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
        "printf 'a\tb\r\n'",
        "a'b'c=1; echo done",
        "echo '\x01\x02'",
    ],
)
def test_escape_command_display_roundtrip(command, path_tmp):
    escaped = escape_command_display(command)
    assert "\n" not in escaped
    original = subprocess.run(
        ["bash", "-c", command], capture_output=True, check=False, cwd=path_tmp
    )
    reproduced = subprocess.run(
        ["bash", "-c", escaped], capture_output=True, check=False, cwd=path_tmp
    )
    assert reproduced.stdout == original.stdout
    assert reproduced.returncode == original.returncode


def test_escape_command_display_no_control_chars():
    command = 'echo "hello" > out.txt'
    assert escape_command_display(command) == command


def test_format_subprocess_escapes_embedded_newline():
    line = format_subprocess("echo a\nb", ".", None, 0, shell=True)
    assert "\n" not in line


def test_is_process_running_self():
    """This very process is trivially running."""
    assert is_process_running(os.getpid())


def test_is_process_running_permission_error(monkeypatch: pytest.MonkeyPatch):
    """A pid owned by another user cannot be signaled, so it is assumed alive."""

    def raise_permission_error(pid: int, sig: int) -> None:
        raise PermissionError

    monkeypatch.setattr(os, "kill", raise_permission_error)
    assert is_process_running(12345)


def test_is_process_running_no_such_process(monkeypatch: pytest.MonkeyPatch):
    """A pid that no longer exists must not keep the next build from starting."""

    def raise_process_lookup_error(pid: int, sig: int) -> None:
        raise ProcessLookupError

    monkeypatch.setattr(os, "kill", raise_process_lookup_error)
    assert not is_process_running(12345)


def test_query_director_log_missing_file(path_tmp: Path):
    path_socket, pid, message = query_director_log(path_tmp / "director.log")
    assert path_socket is None
    assert pid is None
    assert "not found" in message


def test_query_director_log_live_socket(path_tmp: Path):
    path_socket = path_tmp / "director"
    path_socket.touch()
    path_log = path_tmp / "director.log"
    path_log.write_text(f"SOCKET {path_socket}\nPID 12345\nLOG_LEVEL INFO\n")
    assert query_director_log(path_log) == (path_socket, 12345, "")


def test_query_director_log_stale_socket(path_tmp: Path):
    path_log = path_tmp / "director.log"
    path_log.write_text(f"SOCKET {path_tmp / 'director'}\nPID 12345\nLOG_LEVEL INFO\n")
    path_socket, pid, message = query_director_log(path_log)
    assert path_socket is None
    assert pid == 12345
    assert "does not exist" in message


def test_query_director_log_socket_path_with_space(path_tmp: Path):
    """A socket path with a space in it must not be truncated."""
    path_socket = path_tmp / "with space" / "director"
    path_socket.parent.makedirs_p()
    path_socket.touch()
    path_log = path_tmp / "director.log"
    path_log.write_text(f"SOCKET {path_socket}\nPID 12345\n")
    assert query_director_log(path_log)[0] == path_socket


@pytest.mark.parametrize("content", ["", "\n", "Traceback (most recent call last):\n"])
def test_query_director_log_without_socket_line(path_tmp: Path, content: str):
    """A director that crashed before advertising its socket must not confuse the parsing."""
    path_log = path_tmp / "director.log"
    path_log.write_text(content)
    path_socket, pid, message = query_director_log(path_log)
    assert path_socket is None
    assert pid is None
    assert "does not start with SOCKET line" in message


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
