# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Unit tests for `stepup.core.cli.director_log`."""

import os
import subprocess
import sys

import pytest
from core_common import create_socket_path

from stepup.core.cli.director_log import is_process_running, query_director_log, scan_director_log
from stepup.core.path import Path


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
    monkeypatch.setattr("stepup.core.cli.director_log.sys.platform", "linux")

    def raise_permission_error(pid: int, sig: int) -> None:
        raise PermissionError

    monkeypatch.setattr(os, "kill", raise_permission_error)
    assert is_process_running(12345)


def test_is_process_running_no_such_process(monkeypatch: pytest.MonkeyPatch):
    """A pid that no longer exists must not keep the next build from starting."""
    monkeypatch.setattr("stepup.core.cli.director_log.sys.platform", "linux")

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
        "2026-07-27 12:34:57     ERROR      stepup.core.graph.sqlite3  ::  Database is locked\n"
        "sys:1: RuntimeWarning: coroutine 'Watcher.loop' was never awaited\n",
        encoding="utf-8",
    )
    assert scan_director_log(path_log) == [
        "Unretrieved exception: 2026-07-27 12:34:56     ERROR                   asyncio  ::  "
        "Task exception was never retrieved",
        "Logged error: 2026-07-27 12:34:57     ERROR      stepup.core.graph.sqlite3  ::  "
        "Database is locked",
        "Unawaited coroutine: sys:1: RuntimeWarning: coroutine 'Watcher.loop' was never awaited",
    ]
