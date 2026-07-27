# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Tests for stepup.core.interact."""

import argparse
import os
import subprocess
import sys
import threading

import pytest
from path import Path

from stepup.core import interact
from stepup.core.constants import DIRECTOR_LOG
from stepup.core.enums import ReturnCode
from stepup.core.exceptions import InteractError


def _write_director_log(path_tmp: Path, path_socket: Path, pid: int) -> None:
    """Write a director log with the same first lines as `async_main` in `director.py`."""
    (path_tmp / DIRECTOR_LOG).parent.makedirs_p()
    with open(path_tmp / DIRECTOR_LOG, "w") as fh:
        fh.write(f"SOCKET {path_socket}\nPID {pid}\nLOG_LEVEL INFO\n")


def test_get_socket_timeout(path_tmp: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`get_socket` must give up instead of hanging when no director is running."""
    monkeypatch.setenv("STEPUP_ROOT", str(path_tmp))
    monkeypatch.setattr(interact, "GET_SOCKET_TIMEOUT", 0.05)
    monkeypatch.setattr(interact, "GET_SOCKET_INTERVAL", 0.01)
    with pytest.raises(InteractError):
        interact.get_socket()


def test_get_socket_dead_director(path_tmp: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A director log left behind by an exited director must not delay giving up."""
    process = subprocess.Popen([sys.executable, "-c", ""])
    process.wait()
    _write_director_log(path_tmp, path_tmp / "director", process.pid)
    monkeypatch.setenv("STEPUP_ROOT", str(path_tmp))
    monkeypatch.setattr(interact, "GET_SOCKET_TIMEOUT", 0.05)
    monkeypatch.setattr(interact, "GET_SOCKET_INTERVAL", 0.01)
    with pytest.raises(InteractError):
        interact.get_socket()


def test_get_socket_slow_startup(
    path_tmp: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """As long as the director process is alive, `get_socket` must wait without a deadline."""
    path_socket = path_tmp / "director"
    _write_director_log(path_tmp, path_socket, os.getpid())
    monkeypatch.setenv("STEPUP_ROOT", str(path_tmp))
    # The socket shows up long after the timeout, which must not apply on this path.
    monkeypatch.setattr(interact, "GET_SOCKET_TIMEOUT", 0.05)
    monkeypatch.setattr(interact, "GET_SOCKET_INTERVAL", 0.01)
    timer = threading.Timer(0.5, path_socket.touch)
    timer.start()
    try:
        assert interact.get_socket() == path_socket
    finally:
        timer.cancel()
    # The startup notice must not be repeated for every attempt.
    assert capsys.readouterr().err.count("is starting up") == 1


def test_wait_tool_reports_missing_director(
    path_tmp: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """A tool must report a missing director as a short message, not as a traceback."""
    monkeypatch.setenv("STEPUP_ROOT", str(path_tmp))
    monkeypatch.setattr(interact, "GET_SOCKET_TIMEOUT", 0.05)
    monkeypatch.setattr(interact, "GET_SOCKET_INTERVAL", 0.01)
    with pytest.raises(SystemExit) as exc_info:
        interact.wait_tool(argparse.Namespace())
    assert exc_info.value.code == ReturnCode.INTERNAL.value
    assert "ERROR:" in capsys.readouterr().err
