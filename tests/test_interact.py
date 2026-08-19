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
from stepup.core.tool import ToolFunc


def _write_director_log(path_tmp: Path, socket_path: Path, pid: int) -> None:
    """Write a director log with the same first lines as `async_main` in `director.py`."""
    (path_tmp / DIRECTOR_LOG).parent.makedirs_p()
    with open(path_tmp / DIRECTOR_LOG, "w") as fh:
        fh.write(f"SOCKET {socket_path}\nPID {pid}\nLOG_LEVEL INFO\n")


def _wait_tool(argv: list[str]) -> tuple[ToolFunc, argparse.Namespace]:
    """Register the `wait` subcommand and parse `argv` with it, as `stepup` itself would."""
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers()
    tool_func = interact.add_wait_subcommand(subparsers, loader=None)
    return tool_func, parser.parse_args(argv)


class FakeClient:
    """Stand-in for the RPC client, which records whether it was closed."""

    def __init__(self, close_exc: Exception | None = None) -> None:
        self.close_exc = close_exc
        self.closed = False

    def close(self) -> None:
        self.closed = True
        if self.close_exc is not None:
            raise self.close_exc


def _fake_director(monkeypatch: pytest.MonkeyPatch, client: FakeClient) -> None:
    """Let `_connect_director` skip the socket lookup and hand out `client`."""
    monkeypatch.setattr(interact, "wait_for_director_socket", lambda: Path("director.socket"))
    monkeypatch.setattr(interact, "get_rpc_client", lambda socket: client)


def test_wait_for_director_socket_timeout(path_tmp: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`wait_for_director_socket` must give up instead of hanging when no director is running."""
    monkeypatch.setenv("STEPUP_ROOT", str(path_tmp))
    monkeypatch.setattr(interact, "WAIT_FOR_SOCKET_TIMEOUT", 0.05)
    monkeypatch.setattr(interact, "WAIT_FOR_SOCKET_INTERVAL", 0.01)
    with pytest.raises(ToolError):
        interact.wait_for_director_socket()


def test_wait_for_director_socket_dead_director(
    path_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A director log left behind by an exited director must not delay giving up."""
    process = subprocess.Popen([sys.executable, "-c", ""])
    process.wait()
    _write_director_log(path_tmp, path_tmp / "director", process.pid)
    monkeypatch.setenv("STEPUP_ROOT", str(path_tmp))
    monkeypatch.setattr(interact, "WAIT_FOR_SOCKET_TIMEOUT", 0.05)
    monkeypatch.setattr(interact, "WAIT_FOR_SOCKET_INTERVAL", 0.01)
    with pytest.raises(ToolError):
        interact.wait_for_director_socket()


def test_wait_for_director_socket_repeats_no_message(
    path_tmp: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """An unchanged situation must be reported once, not on every attempt."""
    monkeypatch.setenv("STEPUP_ROOT", str(path_tmp))
    monkeypatch.setattr(interact, "WAIT_FOR_SOCKET_TIMEOUT", 0.05)
    monkeypatch.setattr(interact, "WAIT_FOR_SOCKET_INTERVAL", 0.001)
    with pytest.raises(ToolError):
        interact.wait_for_director_socket()
    assert capsys.readouterr().err.count("could not be read") == 1


def test_wait_for_director_socket_slow_startup(
    path_tmp: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """As long as the director process is alive, the socket must be waited for without deadline."""
    socket_path = path_tmp / "director"
    _write_director_log(path_tmp, socket_path, os.getpid())
    monkeypatch.setenv("STEPUP_ROOT", str(path_tmp))
    # The socket shows up long after the timeout, which must not apply on this path.
    monkeypatch.setattr(interact, "WAIT_FOR_SOCKET_TIMEOUT", 0.05)
    monkeypatch.setattr(interact, "WAIT_FOR_SOCKET_INTERVAL", 0.01)
    timer = threading.Timer(0.5, socket_path.touch)
    timer.start()
    try:
        assert interact.wait_for_director_socket() == socket_path
    finally:
        timer.cancel()
    # The startup notice must not be repeated for every attempt.
    assert capsys.readouterr().err.count("is starting up") == 1


def test_wait_subcommand_parses_update_flag() -> None:
    """`stepup wait -u PATH` must set args.update and leave args.delete unset."""
    _, args = _wait_tool(["wait", "-u", "foo.txt"])
    assert args.update == "foo.txt"
    assert args.delete is None


def test_wait_subcommand_parses_delete_flag() -> None:
    """`stepup wait -d PATH` must set args.delete and leave args.update unset."""
    _, args = _wait_tool(["wait", "-d", "foo.txt"])
    assert args.delete == "foo.txt"
    assert args.update is None


def test_wait_subcommand_rejects_update_and_delete_together() -> None:
    """`-u` and `-d` are mutually exclusive."""
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers()
    interact.add_wait_subcommand(subparsers, loader=None)
    with pytest.raises(SystemExit):
        parser.parse_args(["wait", "-u", "a.txt", "-d", "b.txt"])


def test_wait_tool_reports_missing_director(
    path_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing director is a mistake the user can fix, so it must be a `ToolError`."""
    monkeypatch.setenv("STEPUP_ROOT", str(path_tmp))
    monkeypatch.setattr(interact, "WAIT_FOR_SOCKET_TIMEOUT", 0.05)
    monkeypatch.setattr(interact, "WAIT_FOR_SOCKET_INTERVAL", 0.01)
    tool_func, args = _wait_tool(["wait"])
    with pytest.raises(ToolError, match="does not seem to be running"):
        tool_func(args)


def test_connect_director_passes_usage_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A director-side usage error passes through, instead of becoming a connection problem.

    The director was reached just fine: it is the call itself that was rejected,
    so the `Could not connect ...` wording of the `RPCError` clause would be wrong.
    """
    client = FakeClient()
    _fake_director(monkeypatch, client)
    with pytest.raises(GraphError, match="boom"), interact._connect_director():
        raise GraphError("boom")
    assert not client.closed


def test_connect_director_wraps_rpc_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failure to reach the director becomes a `ToolError` that names the cause."""
    _fake_director(monkeypatch, FakeClient())
    with (
        pytest.raises(ToolError, match="Could not connect to the StepUp director: no reply"),
        interact._connect_director(),
    ):
        raise RPCError("no reply")


def test_connect_director_closes_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """A conversation that ended well is closed, so the director can wind down its side."""
    client = FakeClient()
    _fake_director(monkeypatch, client)
    with interact._connect_director() as connected:
        assert connected is client
    assert client.closed


def test_connect_director_ignores_failing_close(monkeypatch: pytest.MonkeyPatch) -> None:
    """A director that is already gone after a shutdown must not turn into an error."""
    client = FakeClient(BrokenPipeError("gone"))
    _fake_director(monkeypatch, client)
    with interact._connect_director():
        pass
    assert client.closed
