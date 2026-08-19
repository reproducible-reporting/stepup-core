# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Tests for the error policy of the `stepup` command line interface.

Every subcommand relies on `main` to turn an exception into an exit code and a message,
so these tests pin that translation down once, independently of any specific tool.
"""

import argparse
import sys

import pytest

from stepup.core import __main__ as cli
from stepup.core.config import ConfigProblem
from stepup.core.enums import ReturnCode
from stepup.core.exceptions import ConfigError, ConsistencyError, GraphError, ToolError


@pytest.fixture
def not_debug(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run without `STEPUP_DEBUG`, which is what a user normally has."""
    monkeypatch.setenv("STEPUP_DEBUG", "0")


class FakeLoader:
    """A configuration loader without problems, so that `_run_subcommand` proceeds to the tool."""

    def problems(self) -> list[ConfigProblem]:
        return []


def _raise_in_tool(monkeypatch: pytest.MonkeyPatch, exc: BaseException) -> None:
    """Make the parsing and dispatching of a subcommand fail with the given exception."""

    def fake_main() -> None:
        raise exc

    monkeypatch.setattr(cli, "_run_subcommand", fake_main)


@pytest.mark.parametrize(
    "exc", [ToolError("boom"), ConfigError("boom"), GraphError("boom")], ids=type
)
def test_usage_error_becomes_short_message(
    exc: Exception,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
    not_debug: None,
) -> None:
    """Every `UsageError` is reported the same way, whichever subcommand raised it."""
    _raise_in_tool(monkeypatch, exc)
    with pytest.raises(SystemExit) as exc_info:
        cli.main()
    assert exc_info.value.code == ReturnCode.INTERNAL.value
    captured = capsys.readouterr()
    assert captured.err.strip() == "ERROR: boom"
    assert "Traceback" not in captured.err


def test_usage_error_keeps_traceback_with_debug(monkeypatch: pytest.MonkeyPatch) -> None:
    """`STEPUP_DEBUG` is the only way to find out where a usage error was raised."""
    monkeypatch.setenv("STEPUP_DEBUG", "1")
    _raise_in_tool(monkeypatch, ToolError("boom"))
    with pytest.raises(ToolError, match="boom"):
        cli.main()


def test_internal_error_keeps_traceback(monkeypatch: pytest.MonkeyPatch, not_debug: None) -> None:
    """An error that is not a `UsageError` points at a bug in StepUp, so it keeps its traceback."""
    _raise_in_tool(monkeypatch, ConsistencyError("boom"))
    with pytest.raises(ConsistencyError, match="boom"):
        cli.main()


def test_interrupt_becomes_short_message(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, not_debug: None
) -> None:
    """Ctrl-C ends a subcommand with the same bit that an aborted build sets."""
    _raise_in_tool(monkeypatch, KeyboardInterrupt())
    with pytest.raises(SystemExit) as exc_info:
        cli.main()
    assert exc_info.value.code == ReturnCode.INTERRUPTED.value
    assert capsys.readouterr().err.strip() == "ERROR: Interrupted."


def test_exit_of_tool_passes_through(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, not_debug: None
) -> None:
    """A tool decides its own exit code with `sys.exit`, e.g. the bit flags of `stepup build`."""
    _raise_in_tool(monkeypatch, SystemExit(ReturnCode.FAILED.value | ReturnCode.DRAINED.value))
    with pytest.raises(SystemExit) as exc_info:
        cli.main()
    assert exc_info.value.code == 36
    assert capsys.readouterr().err == ""


def test_tool_return_value_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    """A tool that completes exits with code 0, whatever it hands back.

    A `ToolFunc` returns nothing, so a stray return value may not reach the exit code,
    the way it did when the exit code of a subcommand was its return value.
    """
    calls = []

    def fake_tool(args: argparse.Namespace) -> int:
        calls.append(args)
        return 42

    parser = argparse.ArgumentParser(prog="stepup")
    subparsers = parser.add_subparsers(dest="tool", required=False)
    subparsers.add_parser("faketool").set_defaults(tool_func=fake_tool)
    monkeypatch.setattr(cli, "_setup_cli", lambda: (parser, FakeLoader()))
    monkeypatch.setattr(sys, "argv", ["stepup", "faketool"])
    cli.main()
    assert len(calls) == 1


def test_no_subcommand_prints_help(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, not_debug: None
) -> None:
    """Running `stepup` without a subcommand explains the usage instead of doing nothing."""
    monkeypatch.setattr(sys, "argv", ["stepup"])
    with pytest.raises(SystemExit) as exc_info:
        cli.main()
    assert exc_info.value.code == ReturnCode.INTERNAL.value
    assert "usage: stepup" in capsys.readouterr().out
