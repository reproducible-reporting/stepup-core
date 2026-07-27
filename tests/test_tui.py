# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Tests for stepup.core.tui."""

import argparse
import contextlib
import os
import signal

import attrs
import pytest
from path import Path

from stepup.core.enums import ReturnCode
from stepup.core.exceptions import TUIError
from stepup.core.tui import (
    TerminalSignalHandler,
    _normalize_targets,
    _resolve_root_and_targets,
    _terminal_broadcasts,
    build_tool,
    merge_resources,
)


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


def test_normalize_targets_trailing_slash_preserved(path_tmp: Path) -> None:
    """A raw target ending in `os.sep` is classified as a directory target, not rejected."""
    with contextlib.chdir(path_tmp):
        targets, target_dirs = _normalize_targets(["subdir/"], path_tmp)
    assert targets == []
    assert target_dirs == [Path("subdir/")]


def test_normalize_targets_dir_no_existence_check(path_tmp: Path) -> None:
    """A directory target need not exist on disk (a clean checkout is the normal case)."""
    with contextlib.chdir(path_tmp):
        targets, target_dirs = _normalize_targets(["out/report/"], path_tmp)
    assert targets == []
    assert target_dirs == [Path("out/report/")]


def test_normalize_targets_dir_dotdot_normalizes_with_slash_reapplied(path_tmp: Path) -> None:
    """`sub/x/../y/` normalizes to `sub/y/`, with the trailing slash re-applied afterward."""
    with contextlib.chdir(path_tmp):
        targets, target_dirs = _normalize_targets(["sub/x/../y/"], path_tmp)
    assert targets == []
    assert target_dirs == [Path("sub/y/")]


def test_normalize_targets_dir_leading_affix_not_reapplied(path_tmp: Path) -> None:
    """A leading `./` is stripped, not re-applied: `File` labels have no leading `./`."""
    with contextlib.chdir(path_tmp):
        targets, target_dirs = _normalize_targets(["./sub/dir/"], path_tmp)
    assert targets == []
    assert target_dirs == [Path("sub/dir/")]


def test_normalize_targets_existing_directory_is_file_target(path_tmp: Path) -> None:
    """A slashless target is an exact-file target, even if it names an existing directory."""
    (path_tmp / "subdir").makedirs_p()
    with contextlib.chdir(path_tmp):
        targets, target_dirs = _normalize_targets(["subdir"], path_tmp)
    assert targets == [Path("subdir")]
    assert target_dirs == []


def test_normalize_targets_empty_string(path_tmp: Path) -> None:
    with contextlib.chdir(path_tmp), pytest.raises(TUIError):
        _normalize_targets([""], path_tmp)


@pytest.mark.parametrize("raw_target", ["..hidden.txt", "..bak/out.txt"])
def test_normalize_targets_dotdot_prefixed_name(path_tmp: Path, raw_target: str) -> None:
    # A target whose name merely starts with ".." (not a "../" parent-traversal component)
    # must be accepted, not mistaken for an outside-root path.
    with contextlib.chdir(path_tmp):
        targets, target_dirs = _normalize_targets([raw_target], path_tmp)
    assert targets == [Path(raw_target)]
    assert target_dirs == []


def test_normalize_targets_happy_path(path_tmp: Path) -> None:
    (path_tmp / "sub").makedirs_p()
    with contextlib.chdir(path_tmp / "sub"):
        targets, target_dirs = _normalize_targets(["../out.txt", "here.txt"], path_tmp)
    assert targets == [Path("out.txt"), Path("sub/here.txt")]
    assert target_dirs == []


def test_normalize_targets_mixed_exact_and_dir(path_tmp: Path) -> None:
    with contextlib.chdir(path_tmp):
        targets, target_dirs = _normalize_targets(["out.txt", "sub/"], path_tmp)
    assert targets == [Path("out.txt")]
    assert target_dirs == [Path("sub/")]


def test_resolve_root_and_targets_relative_root(
    path_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A relative `STEPUP_ROOT` is absolutized against the original cwd, before any `cd()`."""
    (path_tmp / "proj").makedirs_p()
    monkeypatch.setenv("STEPUP_ROOT", "proj")
    with contextlib.chdir(path_tmp):
        stepup_root, targets, target_dirs = _resolve_root_and_targets(["proj/out.txt"])
    assert stepup_root == path_tmp / "proj"
    assert targets == [Path("out.txt")]
    assert target_dirs == []


def test_resolve_root_and_targets_unset_root(
    path_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When `STEPUP_ROOT` is unset, the project root falls back to the current directory."""
    monkeypatch.delenv("STEPUP_ROOT", raising=False)
    with contextlib.chdir(path_tmp):
        stepup_root, targets, target_dirs = _resolve_root_and_targets(["out.txt"])
    assert stepup_root == path_tmp
    assert targets == [Path("out.txt")]
    assert target_dirs == []


def test_resolve_root_and_targets_absolute_root_from_subdir(
    path_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An absolute `STEPUP_ROOT` is honored even when invoked from a subdirectory."""
    (path_tmp / "proj" / "sub").makedirs_p()
    monkeypatch.setenv("STEPUP_ROOT", str(path_tmp / "proj"))
    with contextlib.chdir(path_tmp / "proj" / "sub"):
        stepup_root, targets, target_dirs = _resolve_root_and_targets(["here.txt"])
    assert stepup_root == path_tmp / "proj"
    assert targets == [Path("sub/here.txt")]
    assert target_dirs == []


@attrs.define
class FakeReporterHandler:
    """Records the reports made by `TerminalSignalHandler`."""

    reports: list[tuple[str, str]] = attrs.field(init=False, factory=list)

    def report(self, action: str, description: str, pages: list) -> None:
        self.reports.append((action, description))


@attrs.define
class FakeProcess:
    """Records the signals sent to a stand-in for the director subprocess."""

    pid: int = attrs.field(default=1234)
    signals: list[int] = attrs.field(init=False, factory=list)

    def send_signal(self, sig: int) -> None:
        self.signals.append(sig)

    def kill(self) -> None:
        self.signals.append(signal.SIGKILL)


@pytest.fixture
def signal_handler(monkeypatch: pytest.MonkeyPatch) -> TerminalSignalHandler:
    """A handler whose last-resort kill timer is disabled (no running event loop in tests)."""
    monkeypatch.setattr(TerminalSignalHandler, "_arm_kill_timer", lambda self: None)
    return TerminalSignalHandler(FakeReporterHandler(), FakeProcess())


def test_signal_handler_sigint_not_forwarded_when_broadcast(
    signal_handler: TerminalSignalHandler, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ctrl-C reaches the director through the terminal, so forwarding it would duplicate it."""
    monkeypatch.setattr("stepup.core.tui._terminal_broadcasts", lambda sig, pid: True)
    signal_handler.handle(signal.SIGINT)
    assert signal_handler.process_director.signals == []
    assert signal_handler.signum == signal.SIGINT


def test_signal_handler_sigint_forwarded_when_not_broadcast(
    signal_handler: TerminalSignalHandler, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `kill -INT` on a backgrounded StepUp reaches only the TUI, so it must be forwarded."""
    monkeypatch.setattr("stepup.core.tui._terminal_broadcasts", lambda sig, pid: False)
    signal_handler.handle(signal.SIGINT)
    assert signal_handler.process_director.signals == [signal.SIGINT]


def test_signal_handler_sigterm_always_forwarded(signal_handler: TerminalSignalHandler) -> None:
    """The terminal never generates SIGTERM, so the director must be told separately."""
    signal_handler.handle(signal.SIGTERM)
    assert signal_handler.process_director.signals == [signal.SIGTERM]
    assert signal_handler.signum == signal.SIGTERM


def test_signal_handler_third_signal_kills_director(
    signal_handler: TerminalSignalHandler,
) -> None:
    """An impatient user must always be able to get the shell prompt back."""
    for _ in range(3):
        signal_handler.handle(signal.SIGTERM)
    assert signal_handler.process_director.signals[-1] == signal.SIGKILL
    assert signal_handler.reporter_handler.reports[-1][0] == "ERROR"


def test_signal_handler_reports_once(signal_handler: TerminalSignalHandler) -> None:
    """Only the first signal explains what is happening; repeats must not spam the terminal."""
    signal_handler.handle(signal.SIGTERM)
    signal_handler.handle(signal.SIGTERM)
    interrupted = [
        rep for rep in signal_handler.reporter_handler.reports if "Interrupted" in rep[1]
    ]
    assert len(interrupted) == 1


def test_signal_handler_signum_unset_without_signal(
    signal_handler: TerminalSignalHandler,
) -> None:
    """`signum` is what tells `async_build` to set the INTERRUPTED bit."""
    assert signal_handler.signum is None


def test_terminal_broadcasts_never_for_sigterm() -> None:
    """SIGTERM is never generated by the terminal driver, whatever the process group is."""
    assert not _terminal_broadcasts(signal.SIGTERM, os.getpid())


def test_terminal_broadcasts_without_controlling_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without a controlling terminal, nothing was broadcast, so the signal must be forwarded."""

    def raise_oserror(path: str) -> None:
        raise OSError("no controlling terminal")

    monkeypatch.setattr("builtins.open", raise_oserror)
    assert not _terminal_broadcasts(signal.SIGINT, 1)


def test_build_tool_tui_error_prints_short_message(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """A `TUIError` raised before the director starts must not dump a traceback."""

    async def raise_tui_error(args: argparse.Namespace, default_resources: str) -> None:
        raise TUIError("Target is foobar: foobar.txt")

    monkeypatch.setattr("stepup.core.tui.async_build", raise_tui_error)
    with pytest.raises(SystemExit) as excinfo:
        build_tool(argparse.Namespace(targets=["foobar.txt"]), "")
    assert excinfo.value.code == ReturnCode.INTERNAL.value
    captured = capsys.readouterr()
    assert captured.err.strip() == "ERROR: Target is foobar: foobar.txt"
    assert "Traceback" not in captured.err
