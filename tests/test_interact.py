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
from stepup.core.exceptions import GraphError, RPCError, ToolError


def _write_director_log(path_tmp: Path, socket_path: Path, pid: int) -> None:
    """Write a director log with the same first lines as `async_main` in `director.py`."""
    (path_tmp / DIRECTOR_LOG).parent.makedirs_p()
    with open(path_tmp / DIRECTOR_LOG, "w") as fh:
        fh.write(f"SOCKET {socket_path}\nPID {pid}\nLOG_LEVEL INFO\n")


def test_get_socket_timeout(path_tmp: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`get_socket` must give up instead of hanging when no director is running."""
    monkeypatch.setenv("STEPUP_ROOT", str(path_tmp))
    monkeypatch.setattr(interact, "GET_SOCKET_TIMEOUT", 0.05)
    monkeypatch.setattr(interact, "GET_SOCKET_INTERVAL", 0.01)
    with pytest.raises(ToolError):
        interact.get_socket()


def test_get_socket_dead_director(path_tmp: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A director log left behind by an exited director must not delay giving up."""
    process = subprocess.Popen([sys.executable, "-c", ""])
    process.wait()
    _write_director_log(path_tmp, path_tmp / "director", process.pid)
    monkeypatch.setenv("STEPUP_ROOT", str(path_tmp))
    monkeypatch.setattr(interact, "GET_SOCKET_TIMEOUT", 0.05)
    monkeypatch.setattr(interact, "GET_SOCKET_INTERVAL", 0.01)
    with pytest.raises(ToolError):
        interact.get_socket()


def test_get_socket_slow_startup(
    path_tmp: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """As long as the director process is alive, `get_socket` must wait without a deadline."""
    socket_path = path_tmp / "director"
    _write_director_log(path_tmp, socket_path, os.getpid())
    monkeypatch.setenv("STEPUP_ROOT", str(path_tmp))
    # The socket shows up long after the timeout, which must not apply on this path.
    monkeypatch.setattr(interact, "GET_SOCKET_TIMEOUT", 0.05)
    monkeypatch.setattr(interact, "GET_SOCKET_INTERVAL", 0.01)
    timer = threading.Timer(0.5, socket_path.touch)
    timer.start()
    try:
        assert interact.get_socket() == socket_path
    finally:
        timer.cancel()
    # The startup notice must not be repeated for every attempt.
    assert capsys.readouterr().err.count("is starting up") == 1


def test_wait_subcommand_parses_update_flag() -> None:
    """`stepup wait -u PATH` must set args.update and leave args.delete unset."""
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers()
    interact.wait_subcommand(subparsers, loader=None)
    args = parser.parse_args(["wait", "-u", "foo.txt"])
    assert args.update == "foo.txt"
    assert args.delete is None


def test_wait_subcommand_parses_delete_flag() -> None:
    """`stepup wait -d PATH` must set args.delete and leave args.update unset."""
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers()
    interact.wait_subcommand(subparsers, loader=None)
    args = parser.parse_args(["wait", "-d", "foo.txt"])
    assert args.delete == "foo.txt"
    assert args.update is None


def test_wait_subcommand_rejects_update_and_delete_together() -> None:
    """`-u` and `-d` are mutually exclusive."""
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers()
    interact.wait_subcommand(subparsers, loader=None)
    with pytest.raises(SystemExit):
        parser.parse_args(["wait", "-u", "a.txt", "-d", "b.txt"])


def test_wait_tool_reports_missing_director(
    path_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing director is a mistake the user can fix, so it must be a `ToolError`."""
    monkeypatch.setenv("STEPUP_ROOT", str(path_tmp))
    monkeypatch.setattr(interact, "GET_SOCKET_TIMEOUT", 0.05)
    monkeypatch.setattr(interact, "GET_SOCKET_INTERVAL", 0.01)
    with pytest.raises(ToolError, match="does not seem to be running"):
        interact.wait_tool(argparse.Namespace())


def test_translate_connection_errors_passes_usage_error() -> None:
    """A director-side usage error passes through, instead of becoming a connection problem.

    The director was reached just fine: it is the call itself that was rejected,
    so the `Could not connect ...` wording of the `RPCError` clause would be wrong.
    """

    @interact._translate_connection_errors
    def tool(args: argparse.Namespace) -> None:
        raise GraphError("boom")

    with pytest.raises(GraphError, match="boom"):
        tool(argparse.Namespace())


def test_translate_connection_errors_wraps_rpc_error() -> None:
    """A failure to reach the director becomes a `ToolError` that names the cause."""

    @interact._translate_connection_errors
    def tool(args: argparse.Namespace) -> None:
        raise RPCError("no reply")

    with pytest.raises(ToolError, match="Could not connect to the StepUp director: no reply"):
        tool(argparse.Namespace())
