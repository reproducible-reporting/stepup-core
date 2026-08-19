# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Tests for stepup.core.tui."""

import argparse
import asyncio
import contextlib
import os
import pty
import signal
import sys
import termios
from collections.abc import AsyncGenerator, Callable, Generator
from decimal import Decimal
from types import SimpleNamespace

import attrs
import pytest
from path import Path

from stepup.core.asyncio import stoppable_iterator, wait_for_path
from stepup.core.config import ConfigLoader
from stepup.core.constants import PERF_DATA
from stepup.core.director import DirectorHandler
from stepup.core.enums import ReturnCode
from stepup.core.exceptions import PathError, ToolError
from stepup.core.rpc import allow_rpc, serve_socket_rpc
from stepup.core.tui import (
    _KEY_ACTIONS,
    _KEY_STROKE_HELP,
    RawTerminal,
    SuspendHandler,
    TerminalSignalHandler,
    _add_build_parser,
    _async_build,
    _build_director_argv,
    _build_tool,
    _check_no_running_director,
    _deprecated_boot_tool,
    _iter_keystrokes,
    _normalize_targets,
    _report_director_log_problems,
    _reset_stepup_dir,
    _terminal_broadcasts,
    keyboard,
)


def _build_test_build_parser(loader: ConfigLoader) -> argparse.ArgumentParser:
    """Build a `stepup build` parser through `_add_build_parser`, for parser-level tests."""
    main_parser = argparse.ArgumentParser(prog="stepup")
    subparsers = main_parser.add_subparsers(dest="tool")
    _add_build_parser(subparsers, loader, "build", "Build the StepUp workflow.")
    return main_parser


def test_add_build_parser_resources_no_cli_no_config_no_env() -> None:
    """With nothing set anywhere, `--resources` stays `None`."""
    loader = ConfigLoader("stepup", environ={})
    parser = _build_test_build_parser(loader)
    assert parser.parse_args(["build"]).resources is None


def test_add_build_parser_resources_config_file_default(path_tmp: Path) -> None:
    """A config-file default survives untouched when `-r` is not given."""
    cfg = path_tmp / "stepup.toml"
    cfg.write_bytes(b'[build]\nresources = "cpu:4"\n')
    loader = ConfigLoader("stepup", config_paths=[cfg], environ={})
    parser = _build_test_build_parser(loader)
    assert parser.parse_args(["build"]).resources == "cpu:4"


def test_add_build_parser_resources_env_beats_config_file(path_tmp: Path) -> None:
    """The env var merges on top of the config file default, winning on key collisions."""
    cfg = path_tmp / "stepup.toml"
    cfg.write_bytes(b'[build]\nresources = "cpu:4,gpu:1"\n')
    loader = ConfigLoader("stepup", config_paths=[cfg], environ={"STEPUP_BUILD_RESOURCES": "cpu:8"})
    parser = _build_test_build_parser(loader)
    assert parser.parse_args(["build"]).resources == "cpu:8,gpu:1"


def test_add_build_parser_resources_cli_merges_with_env_default() -> None:
    """A single `-r` merges into the env/config default; the CLI wins on collisions."""
    loader = ConfigLoader("stepup", environ={"STEPUP_BUILD_RESOURCES": "cpu:4,gpu:1"})
    parser = _build_test_build_parser(loader)
    args = parser.parse_args(["build", "-r", "cpu:8,memgb:16"])
    assert args.resources == "cpu:8,gpu:1,memgb:16"


def test_add_build_parser_resources_repeated_cli_merges_left_to_right() -> None:
    """Repeated `-r` occurrences merge left to right rather than the last one winning outright.

    This is a deliberate behavior change from plain argparse `store` semantics
    (last value wins): merging is more useful and matches how config/env values
    already combine with the CLI.
    """
    loader = ConfigLoader("stepup", environ={})
    parser = _build_test_build_parser(loader)
    args = parser.parse_args(["build", "-r", "cpu:4,gpu:1", "-r", "cpu:8"])
    assert args.resources == "cpu:8,gpu:1"


def test_jobs_rejects_non_number(capsys: pytest.CaptureFixture) -> None:
    """A non-numeric `--jobs` value is reported as an argparse error, not a raw traceback."""
    loader = ConfigLoader("stepup", environ={})
    parser = _build_test_build_parser(loader)
    with pytest.raises(SystemExit):
        parser.parse_args(["build", "-j", "abc"])
    stderr = capsys.readouterr().err
    assert "invalid" in stderr
    assert "Traceback" not in stderr


@pytest.mark.parametrize("value", ["0", "-2"])
def test_jobs_rejects_non_positive(value: str, capsys: pytest.CaptureFixture) -> None:
    """A zero or negative `--jobs` value is rejected instead of reaching the director."""
    loader = ConfigLoader("stepup", environ={})
    parser = _build_test_build_parser(loader)
    with pytest.raises(SystemExit):
        parser.parse_args(["build", "-j", value])
    stderr = capsys.readouterr().err
    assert "invalid" in stderr
    assert "Traceback" not in stderr


def test_jobs_accepts_fractional() -> None:
    """A fractional `--jobs` value still parses,
    since it triggers the multiply-by-cores behavior.
    """
    loader = ConfigLoader("stepup", environ={})
    parser = _build_test_build_parser(loader)
    args = parser.parse_args(["build", "-j", "0.5"])
    assert args.jobs == Decimal("0.5")


def test_jobs_env_var_rejected_cleanly() -> None:
    """An invalid `STEPUP_BUILD_JOBS` env var is reported as a problem naming the env var."""
    loader = ConfigLoader("stepup", environ={"STEPUP_BUILD_JOBS": "abc"})
    parser = _build_test_build_parser(loader)
    (message,) = loader.check()
    assert message.startswith("$STEPUP_BUILD_JOBS: ")
    # The rejected value does not reach the director: the parser keeps its own default.
    assert parser.parse_args(["build"]).jobs == Decimal("1.0")


def test_perf_rejects_non_integer(capsys: pytest.CaptureFixture) -> None:
    """A non-integer `--perf` frequency is rejected here, instead of by `perf record` later.

    Without the type check, the value is handed to `perf record -F`, which aborts before
    the director is even started.
    """
    loader = ConfigLoader("stepup", environ={})
    parser = _build_test_build_parser(loader)
    with pytest.raises(SystemExit):
        parser.parse_args(["build", "--perf=abc"])
    stderr = capsys.readouterr().err
    assert "invalid" in stderr
    assert "Traceback" not in stderr


def test_perf_bare_flag_uses_default_frequency() -> None:
    """A bare `--perf` still yields the constant default frequency, now as an `int`."""
    loader = ConfigLoader("stepup", environ={})
    parser = _build_test_build_parser(loader)
    assert parser.parse_args(["build", "--perf"]).perf == 500


def test_perf_env_var_rejected_cleanly() -> None:
    """An invalid `STEPUP_BUILD_PERF` env var is reported as a problem naming the env var."""
    loader = ConfigLoader("stepup", environ={"STEPUP_BUILD_PERF": "abc"})
    parser = _build_test_build_parser(loader)
    (message,) = loader.check()
    assert message.startswith("$STEPUP_BUILD_PERF: ")
    assert parser.parse_args(["build"]).perf is None


def test_build_parser_help_lists_groups(capsys: pytest.CaptureFixture) -> None:
    """`stepup build --help` sorts the options into the three argument groups."""
    loader = ConfigLoader("stepup", environ={})
    parser = _build_test_build_parser(loader)
    with pytest.raises(SystemExit):
        parser.parse_args(["build", "--help"])
    stdout = capsys.readouterr().out
    assert "build control:" in stdout
    assert "execution environment:" in stdout
    assert "diagnostics and profiling:" in stdout


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
    with contextlib.chdir(path_tmp), pytest.raises(ToolError):
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


def test_async_build_resolves_relative_stepup_root(
    path_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A relative `STEPUP_ROOT` is absolutized against the original cwd, before `cd()`."""
    (path_tmp / "proj").makedirs_p()
    (path_tmp / "proj" / "plan.py").touch()
    monkeypatch.setenv("STEPUP_ROOT", "proj")

    async def raise_not_found(*args, **kwargs) -> None:
        raise FileNotFoundError("director executable not found")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", raise_not_found)
    args = argparse.Namespace(
        targets=[],
        cgroup=False,
        clean=True,
        duration=True,
        explain_rerun=False,
        keep_going=False,
        jobs=Decimal("1.0"),
        joblog=False,
        fix_epoch=True,
        forkserver=False,
        perf=None,
        preload_modules=None,
        progress=False,
        defer_cap=100,
        resources=None,
        sqllog=False,
        watch=False,
        watch_first=False,
        yappi=False,
        log_level="WARNING",
    )
    with contextlib.chdir(path_tmp):
        with pytest.raises(FileNotFoundError):
            asyncio.run(asyncio.wait_for(_async_build(args), timeout=5))
        # The director spawn failure above happens after the preflight `cd()`, so
        # reaching it at all proves STEPUP_ROOT was resolved against the original
        # cwd (path_tmp), not against path_tmp / "proj" itself.
        assert Path.cwd() == path_tmp / "proj"


def test_check_no_running_director_live_pid_raises(
    path_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A socket advertised by a still-live pid raises `ToolError` naming the pid."""
    monkeypatch.setattr("stepup.core.tui.DIRECTOR_LOG", path_tmp / "director.log")
    stale_socket = path_tmp / "stale_socket"
    stale_socket.touch()
    (path_tmp / "director.log").write_text(f"SOCKET{stale_socket}\nPID{os.getpid()}\n")
    with pytest.raises(ToolError, match=f"pid {os.getpid()}"):
        _check_no_running_director()


def test_check_no_running_director_dead_pid_passes(
    path_tmp: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A socket advertised by a pid that is no longer alive does not raise."""
    monkeypatch.setattr("stepup.core.tui.DIRECTOR_LOG", path_tmp / "director.log")
    monkeypatch.setattr("stepup.core.tui.is_process_running", lambda pid: False)
    stale_socket = path_tmp / "stale_socket"
    stale_socket.touch()
    (path_tmp / "director.log").write_text(f"SOCKET{stale_socket}\nPID12345\n")
    _check_no_running_director()
    assert "Ignoring stale socket" in capsys.readouterr().out


def test_check_no_running_director_missing_pid_line_raises(
    path_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A socket with no usable `PID` line still refuses, since it cannot tell."""
    monkeypatch.setattr("stepup.core.tui.DIRECTOR_LOG", path_tmp / "director.log")
    stale_socket = path_tmp / "stale_socket"
    stale_socket.touch()
    (path_tmp / "director.log").write_text(f"SOCKET{stale_socket}\n")
    with pytest.raises(ToolError, match="may still be running"):
        _check_no_running_director()


def test_check_no_running_director_missing_socket_passes(
    path_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing director socket path does not raise."""
    monkeypatch.setattr("stepup.core.tui.DIRECTOR_LOG", path_tmp / "director.log")
    missing_socket = path_tmp / "missing_socket"
    (path_tmp / "director.log").write_text(f"SOCKET{missing_socket}\n")
    _check_no_running_director()


def test_check_no_running_director_empty_log_passes(
    path_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty `DIRECTOR_LOG` does not raise."""
    monkeypatch.setattr("stepup.core.tui.DIRECTOR_LOG", path_tmp / "director.log")
    (path_tmp / "director.log").write_text("")
    _check_no_running_director()


def test_reset_stepup_dir_creates_directory(
    path_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The `.stepup` directory is created."""
    dir_stepup = path_tmp / ".stepup"
    monkeypatch.setattr("stepup.core.tui.STEPUP_DIR", dir_stepup)
    monkeypatch.setattr("stepup.core.tui.DIRECTOR_LOG", dir_stepup / "director.log")
    _reset_stepup_dir()
    assert dir_stepup.is_dir()


def test_reset_stepup_dir_clears_log_files(path_tmp: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Existing `.log` files in `.stepup` are removed."""
    dir_stepup = path_tmp / ".stepup"
    dir_stepup.makedirs_p()
    (dir_stepup / "old.log").touch()
    (dir_stepup / "other.txt").touch()
    monkeypatch.setattr("stepup.core.tui.STEPUP_DIR", dir_stepup)
    monkeypatch.setattr("stepup.core.tui.DIRECTOR_LOG", dir_stepup / "director.log")
    _reset_stepup_dir()
    assert not (dir_stepup / "old.log").exists()
    assert (dir_stepup / "other.txt").exists()


def test_reset_stepup_dir_keeps_log_when_director_runs(
    path_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A refused build must leave `.stepup` untouched, proving the check runs first."""
    dir_stepup = path_tmp / ".stepup"
    dir_stepup.makedirs_p()
    director_log = dir_stepup / "director.log"
    stale_socket = path_tmp / "stale_socket"
    stale_socket.touch()
    director_log.write_text(f"SOCKET{stale_socket}\n")
    (dir_stepup / "other.log").touch()
    monkeypatch.setattr("stepup.core.tui.STEPUP_DIR", dir_stepup)
    monkeypatch.setattr("stepup.core.tui.DIRECTOR_LOG", director_log)
    with pytest.raises(ToolError, match="may still be running"):
        _reset_stepup_dir()
    assert director_log.is_file()
    assert (dir_stepup / "other.log").is_file()


def _base_build_args(**overrides) -> argparse.Namespace:
    """Build a `stepup build` `Namespace` with defaults matching the parser, for argv tests."""
    defaults = {
        "targets": [],
        "cgroup": False,
        "clean": True,
        "duration": True,
        "explain_rerun": False,
        "keep_going": False,
        "jobs": Decimal("1.0"),
        "joblog": False,
        "fix_epoch": True,
        "forkserver": False,
        "perf": None,
        "preload_modules": None,
        "progress": True,
        "defer_cap": 100,
        "resources": None,
        "sqllog": False,
        "watch": False,
        "watch_first": False,
        "yappi": False,
        "log_level": "WARNING",
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _call_build_director_argv(
    args: argparse.Namespace,
    *,
    targets: list[Path] | None = None,
    target_dirs: list[Path] | None = None,
    live_progress: bool = False,
) -> list[str]:
    return _build_director_argv(
        args,
        targets or [],
        target_dirs or [],
        Path("/tmp/sockets/director"),
        Path("/tmp/sockets/reporter"),
        live_progress=live_progress,
    )


def test_build_director_argv_baseline() -> None:
    """The baseline argv contains the interpreter, module, sockets, and always-on flags."""
    argv = _call_build_director_argv(_base_build_args())
    assert argv == [
        sys.executable,
        "-m",
        "stepup.core.director",
        Path("/tmp/sockets/director"),
        "--reporter=/tmp/sockets/reporter",
        "--jobs=1.0",
        "--defer-cap=100",
        "--log-level=WARNING",
    ]


@pytest.mark.parametrize("jobs", [Decimal("4"), Decimal("0.5"), Decimal("1.0")])
def test_build_director_argv_jobs_verbatim(jobs: Decimal) -> None:
    """The `--jobs` value is forwarded unchanged: the director interprets it, not the TUI."""
    argv = _call_build_director_argv(_base_build_args(jobs=jobs))
    assert f"--jobs={jobs}" in argv


@pytest.mark.parametrize(
    ("attr", "flag"),
    [
        ("explain_rerun", "--explain-rerun"),
        ("keep_going", "--keep-going"),
        ("sqllog", "--sqllog"),
        ("joblog", "--joblog"),
        ("watch", "--watch"),
        ("watch_first", "--watch-first"),
        ("yappi", "--yappi"),
        ("forkserver", "--forkserver"),
    ],
)
def test_build_director_argv_plain_boolean_flags(attr: str, flag: str) -> None:
    """A plain boolean flag appears iff the corresponding argument is true."""
    argv_false = _call_build_director_argv(_base_build_args(**{attr: False}))
    argv_true = _call_build_director_argv(_base_build_args(**{attr: True}))
    assert flag not in argv_false
    assert flag in argv_true


@pytest.mark.parametrize(
    ("attr", "true_flag", "false_flag"),
    [
        ("clean", None, "--no-clean"),
        ("duration", None, "--no-duration"),
        ("fix_epoch", None, "--no-fix-epoch"),
    ],
)
def test_build_director_argv_inverted_boolean_flags(
    attr: str, true_flag: str | None, false_flag: str
) -> None:
    """An inverted flag (default True) only appears as `--no-...` when the argument is False."""
    argv_true = _call_build_director_argv(_base_build_args(**{attr: True}))
    argv_false = _call_build_director_argv(_base_build_args(**{attr: False}))
    if true_flag is not None:
        assert true_flag in argv_true
    assert false_flag not in argv_true
    assert false_flag in argv_false


def test_build_director_argv_preload_modules() -> None:
    """`--preload-modules` is only added when a value is given."""
    assert "--preload-modules=foo,bar" not in _call_build_director_argv(_base_build_args())
    argv = _call_build_director_argv(_base_build_args(preload_modules="foo,bar"))
    assert "--preload-modules=foo,bar" in argv


def test_build_director_argv_targets() -> None:
    """Each exact-file and directory target becomes its own `--target=`/`--target-dir=` entry."""
    argv = _call_build_director_argv(
        _base_build_args(),
        targets=[Path("a.txt"), Path("b.txt")],
        target_dirs=[Path("sub/")],
    )
    assert "--target=a.txt" in argv
    assert "--target=b.txt" in argv
    assert "--target-dir=sub/" in argv


def test_build_director_argv_perf() -> None:
    """`--perf` prefixes the command with `perf record` and adds `-X perf` after the interpreter."""
    argv = _call_build_director_argv(_base_build_args(perf=200))
    idx_exe = argv.index(sys.executable)
    assert argv[:idx_exe] == ["perf", "record", "-F", "200", "-i", "-g", "-o", PERF_DATA]
    assert argv[idx_exe + 1 : idx_exe + 3] == ["-X", "perf"]


def test_build_director_argv_no_perf() -> None:
    """Without `--perf`, the interpreter is the very first argv entry."""
    argv = _call_build_director_argv(_base_build_args(perf=None))
    assert argv[0] == sys.executable
    assert "-X" not in argv


def test_build_director_argv_resources_empty() -> None:
    """An empty resolved resources string produces no `--resources` flag."""
    argv = _call_build_director_argv(_base_build_args(resources=""))
    assert not any(entry.startswith("--resources") for entry in argv)


def test_build_director_argv_resources_nonempty() -> None:
    """A non-empty resolved resources string is passed through as `--resources=...`."""
    argv = _call_build_director_argv(_base_build_args(resources="cpu:4,gpu:1"))
    assert "--resources=cpu:4,gpu:1" in argv


def test_build_director_argv_live_progress_false() -> None:
    """`live_progress=False` never adds `--live-progress`, regardless of `args.progress`."""
    argv = _call_build_director_argv(_base_build_args(progress=True), live_progress=False)
    assert "--live-progress" not in argv


def test_build_director_argv_live_progress_true() -> None:
    argv = _call_build_director_argv(_base_build_args(), live_progress=True)
    assert "--live-progress" in argv


def test_build_director_argv_cgroup(monkeypatch: pytest.MonkeyPatch) -> None:
    """`--cgroup` prepends the `cgroup_scope_prefix()` result and appends `--cgroup` at the end."""
    monkeypatch.setattr("stepup.core.tui.cgroup_scope_prefix", lambda: ["systemd-run", "--scope"])
    argv = _call_build_director_argv(_base_build_args(cgroup=True))
    assert argv[:2] == ["systemd-run", "--scope"]
    assert argv[-1] == "--cgroup"


def test_build_director_argv_no_cgroup() -> None:
    argv = _call_build_director_argv(_base_build_args(cgroup=False))
    assert "--cgroup" not in argv


def test_build_director_argv_does_not_mutate_args() -> None:
    """The function must not mutate `args`, in particular not `args.resources`."""
    args = _base_build_args(resources="cpu:4")
    _call_build_director_argv(args)
    assert args.resources == "cpu:4"


@attrs.define
class FakeReporterHandler:
    """Records the reports and `stop_reporting()` calls
    made by `_async_build`/`TerminalSignalHandler`."""

    live_progress: bool = attrs.field(init=False, default=False)
    reports: list[tuple[str, str]] = attrs.field(init=False, factory=list)
    pages: list[tuple[str, str]] = attrs.field(init=False, factory=list)
    stop_reporting_calls: int = attrs.field(init=False, default=0)
    display_calls: list[str] = attrs.field(init=False, factory=list)

    def report(self, action: str, description: str, pages: list) -> None:
        self.reports.append((action, description))
        self.pages.extend(pages)

    def stop_reporting(self) -> None:
        self.stop_reporting_calls += 1

    def suspend_display(self) -> None:
        self.display_calls.append("suspend")

    def resume_display(self, suspended: float = 0.0) -> None:
        self.display_calls.append("resume")


def _log_record(level: str, message: str) -> str:
    """Format one line the way `director.py` configures `logging.basicConfig` to."""
    return f"2026-07-27 12:34:56  {level:>8s}  {'stepup.core.director':>24s}  ::  {message}\n"


def _report_problems(
    director_log: Path, text: str, monkeypatch: pytest.MonkeyPatch, debug: bool = False
) -> tuple[FakeReporterHandler, int]:
    """Let `_report_director_log_problems` loose on a director log with the given contents."""
    director_log.write_text(text)
    monkeypatch.setattr("stepup.core.tui.DIRECTOR_LOG", director_log)
    monkeypatch.setenv("STEPUP_DEBUG", "1" if debug else "0")
    reporter_handler = FakeReporterHandler()
    returncode = _report_director_log_problems(reporter_handler)
    return reporter_handler, returncode


@pytest.mark.parametrize("level", ["ERROR", "CRITICAL"])
def test_report_director_log_problems_logged_error(
    level: str, path_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A logged error deserves a warning, even when the director exited successfully."""
    director_log = path_tmp / "director.log"
    record = _log_record(level, "Something broke")
    reporter_handler, returncode = _report_problems(
        director_log, _log_record("INFO", "Something happened") + record, monkeypatch
    )
    assert reporter_handler.reports == [("WARNING", f"Problems logged in {director_log}")]
    assert reporter_handler.pages == [("Director log", f"Logged error: {record.strip()}")]
    assert returncode == 0


@pytest.mark.parametrize(
    ("line", "label"),
    [
        (
            "sys:1: RuntimeWarning: coroutine 'Watcher.loop' was never awaited",
            "Unawaited coroutine",
        ),
        ("Task exception was never retrieved", "Unretrieved exception"),
        ("Future exception was never retrieved", "Unretrieved exception"),
        ("Task was destroyed but it is pending!", "Abandoned pending task"),
        ("Exception in callback <TaskWakeupMethWrapper object>", "Exception in callback"),
        ("Exception in thread hash-worker-3:", "Exception in thread"),
        ("Exception ignored in: <function DBSession.__del__>", "Ignored exception"),
    ],
)
def test_report_director_log_problems_symptoms(
    line: str, label: str, path_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dangling work is reported, also when Python wrote it straight to stderr.

    None of these make the director exit with a non-zero return code,
    so the log is the only place where they can be picked up.
    """
    director_log = path_tmp / "director.log"
    reporter_handler, returncode = _report_problems(director_log, line + "\n", monkeypatch)
    assert reporter_handler.reports == [("WARNING", f"Problems logged in {director_log}")]
    assert reporter_handler.pages == [("Director log", f"{label}: {line}")]
    assert returncode == 0


def test_report_director_log_problems_debug_is_fatal(
    path_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With `STEPUP_DEBUG`, a finding is an error that fails the build."""
    director_log = path_tmp / "director.log"
    reporter_handler, returncode = _report_problems(
        director_log, _log_record("ERROR", "Something broke"), monkeypatch, debug=True
    )
    assert reporter_handler.reports == [("ERROR", f"Problems logged in {director_log}")]
    assert returncode == ReturnCode.INTERNAL.value


def test_report_director_log_problems_silent_without_errors(
    path_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A log without errors must not warn, not even about the word `error` in lower case."""
    director_log = path_tmp / "director.log"
    reporter_handler, returncode = _report_problems(
        director_log,
        _log_record("INFO", "Something happened")
        + _log_record("WARNING", "error handling is fine"),
        monkeypatch,
        debug=True,
    )
    assert reporter_handler.reports == []
    assert returncode == 0


@pytest.mark.parametrize("level", ["ERROR", "CRITICAL"])
def test_report_director_log_problems_ignores_header_lines(
    level: str, path_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--log-level=ERROR` must not make every successful build end with a warning.

    Regression test: the director writes its `LOG_LEVEL <level>` header line into
    `DIRECTOR_LOG`, and a plain substring search found `ERROR` in it, so a completely
    clean build reported `Errors logged in .stepup/director.log`.
    """
    director_log = path_tmp / "director.log"
    reporter_handler, returncode = _report_problems(
        director_log,
        f"SOCKET /tmp/stepup-abcd1234/director\nPID 12345\nLOG_LEVEL {level}\n",
        monkeypatch,
    )
    assert reporter_handler.reports == []
    assert returncode == 0


def test_report_director_log_problems_ignores_error_inside_message(
    path_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only the level field counts, not the word `ERROR` somewhere in a message."""
    director_log = path_tmp / "director.log"
    reporter_handler, returncode = _report_problems(
        director_log, _log_record("INFO", "Retrying after a transient ERROR"), monkeypatch
    )
    assert reporter_handler.reports == []
    assert returncode == 0


@attrs.define
class FakeProcess:
    """Records the signals sent to a stand-in for the director subprocess."""

    pid: int = attrs.field(default=1234)
    returncode: int = attrs.field(default=0)
    signals: list[int] = attrs.field(init=False, factory=list)

    def send_signal(self, sig: int) -> None:
        self.signals.append(sig)

    def kill(self) -> None:
        self.signals.append(signal.SIGKILL)

    async def wait(self) -> int:
        return self.returncode


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
    assert signal_handler.sig == signal.SIGINT


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
    assert signal_handler.sig == signal.SIGTERM


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


def test_signal_handler_sig_unset_without_signal(
    signal_handler: TerminalSignalHandler,
) -> None:
    """`sig` is what tells `_async_build` to set the INTERRUPTED bit."""
    assert signal_handler.sig is None


def _run_suspend_handler(
    suspend_handler: SuspendHandler,
    monkeypatch: pytest.MonkeyPatch,
    on_stop: Callable[[], None] | None = None,
) -> list[tuple[int, int]]:
    """Run `SuspendHandler.handle` with the self-stop replaced by a recording stub.

    Really stopping is not an option here: nothing would continue the pytest process again.
    The signal handler installed on the way out is removed, so it cannot outlive the loop
    it was registered with.

    Parameters
    ----------
    suspend_handler
        The handler under test.
    monkeypatch
        The fixture used to replace `os.kill`.
    on_stop
        Called where the process would have stopped, to observe the suspended state.

    Returns
    -------
    stops
        The `(pid, signal)` pairs the handler tried to stop itself with.
    """
    stops = []

    def fake_kill(pid: int, sig: int) -> None:
        stops.append((pid, sig))
        if on_stop is not None:
            on_stop()

    async def scenario() -> None:
        monkeypatch.setattr(os, "kill", fake_kill)
        try:
            suspend_handler.handle()
        finally:
            asyncio.get_running_loop().remove_signal_handler(signal.SIGTSTP)

    asyncio.run(asyncio.wait_for(scenario(), timeout=5))
    return stops


def test_suspend_handler_stops_this_process(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ctrl-Z reaches the director through the terminal, so forwarding it would duplicate it."""
    monkeypatch.setattr("stepup.core.tui._terminal_broadcasts", lambda sig, pid: True)
    suspend_handler = SuspendHandler(FakeReporterHandler(), FakeProcess())
    stops = _run_suspend_handler(suspend_handler, monkeypatch)
    assert stops == [(os.getpid(), signal.SIGTSTP)]
    assert suspend_handler.process_director.signals == []
    assert suspend_handler.reporter_handler.display_calls == ["suspend", "resume"]


def test_suspend_handler_forwards_when_not_broadcast(monkeypatch: pytest.MonkeyPatch) -> None:
    """A `kill -TSTP` reaches only the TUI, so the director must be stopped and continued too."""
    monkeypatch.setattr("stepup.core.tui._terminal_broadcasts", lambda sig, pid: False)
    suspend_handler = SuspendHandler(FakeReporterHandler(), FakeProcess())
    _run_suspend_handler(suspend_handler, monkeypatch)
    assert suspend_handler.process_director.signals == [signal.SIGTSTP, signal.SIGCONT]


def test_suspend_handler_hands_the_terminal_over_and_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """The shell gets a canonical terminal, and raw mode is taken over again on resume."""
    monkeypatch.setattr("stepup.core.tui._terminal_broadcasts", lambda sig, pid: True)
    primary_fd, secondary_fd = pty.openpty()
    try:
        before = termios.tcgetattr(secondary_fd)
        raw_terminal = RawTerminal(secondary_fd)
        raw_terminal.enter()
        suspend_handler = SuspendHandler(FakeReporterHandler(), FakeProcess(), raw_terminal)

        while_stopped = []
        _run_suspend_handler(
            suspend_handler,
            monkeypatch,
            on_stop=lambda: while_stopped.append(termios.tcgetattr(secondary_fd)),
        )

        assert while_stopped == [before]
        after = termios.tcgetattr(secondary_fd)
        assert not after[3] & termios.ICANON
        assert not after[3] & termios.ECHO
    finally:
        os.close(primary_fd)
        os.close(secondary_fd)


def test_suspend_handler_without_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-interactive run has no terminal to hand over, but must still suspend."""
    monkeypatch.setattr("stepup.core.tui._terminal_broadcasts", lambda sig, pid: True)
    suspend_handler = SuspendHandler(FakeReporterHandler(), FakeProcess())
    assert suspend_handler.raw_terminal is None
    stops = _run_suspend_handler(suspend_handler, monkeypatch)
    assert stops == [(os.getpid(), signal.SIGTSTP)]


def test_terminal_broadcasts_never_for_sigterm() -> None:
    """SIGTERM is never generated by the terminal driver, whatever the process group is."""
    assert not _terminal_broadcasts(signal.SIGTERM, os.getpid())


def test_terminal_broadcasts_for_sigtstp_in_foreground(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ctrl-Z is generated by the terminal driver for the whole foreground process group."""

    @contextlib.contextmanager
    def fake_tty(path: str):
        assert path == "/dev/tty"
        yield SimpleNamespace(fileno=lambda: 99)

    monkeypatch.setattr("builtins.open", fake_tty)
    monkeypatch.setattr(os, "tcgetpgrp", lambda fd: 4321)
    monkeypatch.setattr(os, "getpgid", lambda pid: 4321)
    assert _terminal_broadcasts(signal.SIGTSTP, os.getpid())


def test_terminal_broadcasts_without_controlling_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without a controlling terminal, nothing was broadcast, so the signal must be forwarded."""

    def raise_oserror(path: str) -> None:
        raise OSError("no controlling terminal")

    monkeypatch.setattr("builtins.open", raise_oserror)
    assert not _terminal_broadcasts(signal.SIGINT, 1)


def test_build_tool_tui_error_prints_short_message(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """A `ToolError` raised before the director starts must not dump a traceback."""

    async def raise_tui_error(args: argparse.Namespace) -> None:
        raise ToolError("Target is foobar: foobar.txt")

    monkeypatch.setattr("stepup.core.tui._async_build", raise_tui_error)
    assert _build_tool(argparse.Namespace(targets=["foobar.txt"])) == ReturnCode.INTERNAL.value
    captured = capsys.readouterr()
    assert captured.err.strip() == "ERROR: Target is foobar: foobar.txt"
    assert "Traceback" not in captured.err


def test_boot_tool_propagates_return_code(monkeypatch: pytest.MonkeyPatch) -> None:
    """`_deprecated_boot_tool` must return what `_build_tool` returned, not swallow it.

    Regression guard: `_build_tool` returns a value instead of always exiting via
    `sys.exit`, so a bare `_build_tool(args)` call (no `return`) here would make
    `stepup boot` silently exit 0 regardless of the actual outcome.
    """
    monkeypatch.setattr("stepup.core.tui._build_tool", lambda args: 42)
    assert _deprecated_boot_tool(argparse.Namespace()) == 42


def test_async_build_returns_director_returncode(
    path_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`_async_build` returns the director's exit code directly, instead of calling `sys.exit`."""
    (path_tmp / "plan.py").touch()
    fake_reporter_handler = FakeReporterHandler()
    monkeypatch.setattr("stepup.core.tui.ReporterHandler", lambda *a, **kw: fake_reporter_handler)
    fake_process = FakeProcess(returncode=3)

    async def fake_create_subprocess_exec(*args, **kwargs):
        return fake_process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    args = _base_build_args(progress=False)
    with contextlib.chdir(path_tmp):
        returncode = asyncio.run(asyncio.wait_for(_async_build(args), timeout=5))
    assert returncode == 3


def _pre_interrupted_signal_handler(reporter_handler, process_director) -> TerminalSignalHandler:
    """Build a real `TerminalSignalHandler` as if a terminal signal had already arrived.

    Real signal delivery is racy to assert on in a unit test (the OS delivers the signal
    asynchronously with respect to the event loop's own scheduling), so this sets the
    outcome of `TerminalSignalHandler.handle` directly, to exercise the `sig is not None`
    branch of `translate_wait_status` deterministically.
    """
    signal_handler = TerminalSignalHandler(reporter_handler, process_director)
    signal_handler.sig = signal.SIGTERM
    return signal_handler


@pytest.mark.parametrize(
    ("wait_status", "sig", "expected"),
    [
        (0, None, 0),
        (3, None, 3),
        (0, signal.SIGTERM, ReturnCode.INTERRUPTED.value),
        (3, signal.SIGINT, 3 | ReturnCode.INTERRUPTED.value),
        # A wait status of -9 is not a ReturnCode combination, so it becomes INTERNAL.
        (-signal.SIGKILL, None, ReturnCode.INTERNAL.value),
        (
            -signal.SIGKILL,
            signal.SIGTERM,
            ReturnCode.INTERNAL.value | ReturnCode.INTERRUPTED.value,
        ),
    ],
)
def test_translate_wait_status(wait_status: int, sig: signal.Signals | None, expected: int) -> None:
    """The director's wait status becomes a `ReturnCode` flag combination."""
    signal_handler = TerminalSignalHandler(FakeReporterHandler(), FakeProcess())
    signal_handler.sig = sig
    assert signal_handler.translate_wait_status(wait_status) == expected


def test_translate_wait_status_reports_killed_director() -> None:
    """A director killed by a signal is reported by name, not silently turned into a code."""
    signal_handler = TerminalSignalHandler(FakeReporterHandler(), FakeProcess())
    signal_handler.translate_wait_status(-signal.SIGKILL)
    assert signal_handler.reporter_handler.reports == [("ERROR", "Director killed by SIGKILL")]


def test_async_build_ors_interrupted_bit(path_tmp: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The `INTERRUPTED` bit is OR-ed onto whatever the director reported, when `sig` is set."""
    (path_tmp / "plan.py").touch()
    fake_reporter_handler = FakeReporterHandler()
    monkeypatch.setattr("stepup.core.tui.ReporterHandler", lambda *a, **kw: fake_reporter_handler)
    monkeypatch.setattr("stepup.core.tui.TerminalSignalHandler", _pre_interrupted_signal_handler)
    fake_process = FakeProcess(returncode=0)

    async def fake_create_subprocess_exec(*args, **kwargs):
        return fake_process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    args = _base_build_args(progress=False)
    with contextlib.chdir(path_tmp):
        returncode = asyncio.run(asyncio.wait_for(_async_build(args), timeout=5))
    assert returncode == ReturnCode.INTERRUPTED.value


def test_key_stroke_help_mentions_every_key() -> None:
    """The generated help text must mention every key in the dispatch table."""
    for key in _KEY_ACTIONS:
        assert f"{key} = " in _KEY_STROKE_HELP


def test_key_actions_methods_are_allowed_rpc() -> None:
    """Every RPC method name in the table must exist on `DirectorHandler` and be `@allow_rpc`.

    This is the check that would actually catch a typo'd or renamed method name
    in the dispatch table.
    """
    for action in _KEY_ACTIONS.values():
        method = getattr(DirectorHandler, action.method, None)
        assert method is not None, f"DirectorHandler has no method {action.method!r}"
        assert getattr(method, "_allow_rpc", False), f"{action.method!r} is not @allow_rpc"


def _fixed_keystrokes(keys: list[str]) -> Callable[[asyncio.Event], AsyncGenerator[str, None]]:
    """Build a stand-in for `_iter_keystrokes` that yields a fixed sequence, then stops."""

    async def _iter(stop_event: asyncio.Event) -> AsyncGenerator[str, None]:
        for ch in keys:
            yield ch
        stop_event.set()

    return _iter


class _FakeDirectorHandler:
    """Exposes the `DirectorHandler` RPC methods reachable through `_KEY_ACTIONS`."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []

    @allow_rpc
    async def start_build_phase(self) -> None:
        self.calls.append(("start_build_phase", ()))

    @allow_rpc
    async def shutdown(self) -> None:
        self.calls.append(("shutdown", ()))

    @allow_rpc
    async def drain(self) -> None:
        self.calls.append(("drain", ()))

    @allow_rpc
    async def wait_and_shutdown(self) -> None:
        self.calls.append(("wait_and_shutdown", ()))

    @allow_rpc
    async def write_graph(self, path: str) -> None:
        self.calls.append(("write_graph", (path,)))


def test_keyboard_reports_unreachable_director(
    path_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A keystroke sent while the director's socket does not exist is reported, not raised.

    Regression test: `AsyncRPCClient.socket` raises `FileNotFoundError` (or
    `ConnectionRefusedError`) in the ordinary shutdown window. Catching it at the
    per-keystroke connect keeps it from escaping `keyboard`, `asyncio.gather`, and
    `_build_tool`'s `except ToolError` as a traceback instead of the exit code.
    """
    monkeypatch.setattr("stepup.core.tui._iter_keystrokes", _fixed_keystrokes(["r"]))
    fake_reporter_handler = FakeReporterHandler()
    stop_event = asyncio.Event()
    socket_path = path_tmp / "no-such-socket"
    asyncio.run(
        asyncio.wait_for(keyboard(socket_path, fake_reporter_handler, stop_event), timeout=5)
    )
    assert any(
        action == "KEYBOARD" and "key r ignored" in message.lower()
        for action, message in fake_reporter_handler.reports
    )


def test_keyboard_survives_director_disappearing(
    path_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Losing one keystroke to an unreachable director must not stop the loop."""
    monkeypatch.setattr("stepup.core.tui._iter_keystrokes", _fixed_keystrokes(["r", "d"]))
    fake_reporter_handler = FakeReporterHandler()
    stop_event = asyncio.Event()
    socket_path = path_tmp / "no-such-socket"
    asyncio.run(
        asyncio.wait_for(keyboard(socket_path, fake_reporter_handler, stop_event), timeout=5)
    )
    messages = [
        message for action, message in fake_reporter_handler.reports if action == "KEYBOARD"
    ]
    assert any("key r ignored" in message.lower() for message in messages)
    assert any("key d ignored" in message.lower() for message in messages)


class _RaisingDirectorHandler:
    """A director stand-in whose `drain`/`write_graph` methods always raise, once called.

    Used to test the `report_after` ordering contract: the report must (or must not)
    already have happened by the time the failing call's exception reaches the caller.
    """

    def __init__(self) -> None:
        self.called = False

    @allow_rpc
    async def drain(self) -> None:
        self.called = True
        raise RuntimeError("drain failed")

    @allow_rpc
    async def write_graph(self, path: str) -> None:
        self.called = True
        raise RuntimeError("graph failed")


class _RejectingDirectorHandler:
    """A director stand-in whose `write_graph` method rejects the call with a usage error."""

    @allow_rpc
    async def write_graph(self, path: str) -> None:
        raise PathError("bad path")


async def _drive_keyboard(
    handler,
    socket_path: Path,
    keys: list[str],
    reporter_handler,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Serve *handler* over a real RPC socket and run `keyboard` against it with fixed keys."""
    server_stop_event = asyncio.Event()
    server_task = asyncio.create_task(
        serve_socket_rpc(handler, str(socket_path), server_stop_event)
    )
    try:
        await wait_for_path(socket_path, server_stop_event)
        monkeypatch.setattr("stepup.core.tui._iter_keystrokes", _fixed_keystrokes(keys))
        await keyboard(socket_path, reporter_handler, asyncio.Event())
    finally:
        server_stop_event.set()
        await server_task


@pytest.mark.parametrize("key", list(_KEY_ACTIONS))
def test_keyboard_dispatches_each_key(
    key: str, path_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A recognized key opens a connection and calls the matching `DirectorHandler` method."""
    handler = _FakeDirectorHandler()
    socket_path = path_tmp / "director"
    action = _KEY_ACTIONS[key]
    asyncio.run(
        asyncio.wait_for(
            _drive_keyboard(handler, socket_path, [key], FakeReporterHandler(), monkeypatch),
            timeout=5,
        )
    )
    assert handler.calls == [(action.method, action.args)]


def test_keyboard_reports_before_call(path_tmp: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """For a non-`report_after` action, the report happens even when the call then fails.

    `drain` reports before calling, per `KeyAction.report_after`'s documented contract,
    so a failing call must not suppress the report.
    """
    handler = _RaisingDirectorHandler()
    socket_path = path_tmp / "director"
    fake_reporter_handler = FakeReporterHandler()
    asyncio.run(
        asyncio.wait_for(
            _drive_keyboard(handler, socket_path, ["d"], fake_reporter_handler, monkeypatch),
            timeout=5,
        )
    )
    assert ("KEYBOARD", _KEY_ACTIONS["d"].message) in fake_reporter_handler.reports


def test_keyboard_reports_after_call_for_graph(
    path_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No success report is made when the `graph` call raises.

    That is the entire reason `report_after` exists: the message claims `graph.txt` exists.
    """
    handler = _RaisingDirectorHandler()
    socket_path = path_tmp / "director"
    fake_reporter_handler = FakeReporterHandler()
    asyncio.run(
        asyncio.wait_for(
            _drive_keyboard(handler, socket_path, ["g"], fake_reporter_handler, monkeypatch),
            timeout=5,
        )
    )
    assert _KEY_ACTIONS["g"].message not in [
        message for _, message in fake_reporter_handler.reports
    ]


def test_keyboard_reports_director_side_failure(
    path_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A call that raises inside the director is reported, not propagated.

    Regression test: only `OSError` was caught, so the `RPCError` wrapping a director-side
    exception (e.g. `graph` unable to write `graph.txt` because a directory of that name
    exists) escaped the keyboard task. `_supervise_director` awaits that task in its
    `finally`, so the exception replaced the director's return code with a traceback.
    """
    handler = _RaisingDirectorHandler()
    socket_path = path_tmp / "director"
    fake_reporter_handler = FakeReporterHandler()
    asyncio.run(
        asyncio.wait_for(
            _drive_keyboard(handler, socket_path, ["g"], fake_reporter_handler, monkeypatch),
            timeout=5,
        )
    )
    assert handler.called
    assert fake_reporter_handler.reports == [("ERROR", "Key g failed in the director.")]
    # A bug in the director comes with a traceback, which is what the page holds.
    assert [heading for heading, _ in fake_reporter_handler.pages] == ["Traceback"]


def test_keyboard_reports_usage_error_as_a_message(
    path_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rejected call is reported under a `Message` heading, not under `Traceback`.

    The director raised a `UsageError`, which reaches the client as a one-line message
    without a traceback, so the `RPCError` wording below would show an empty-looking block.
    """
    socket_path = path_tmp / "director"
    fake_reporter_handler = FakeReporterHandler()
    asyncio.run(
        asyncio.wait_for(
            _drive_keyboard(
                _RejectingDirectorHandler(), socket_path, ["g"], fake_reporter_handler, monkeypatch
            ),
            timeout=5,
        )
    )
    assert fake_reporter_handler.reports == [("ERROR", "Key g failed in the director.")]
    assert fake_reporter_handler.pages == [("Message", "bad path")]


def test_keyboard_survives_director_side_failure(
    path_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One failing keystroke must not stop the loop from dispatching the next one."""
    handler = _RaisingDirectorHandler()
    socket_path = path_tmp / "director"
    fake_reporter_handler = FakeReporterHandler()
    asyncio.run(
        asyncio.wait_for(
            _drive_keyboard(handler, socket_path, ["g", "d"], fake_reporter_handler, monkeypatch),
            timeout=5,
        )
    )
    actions = [action for action, _ in fake_reporter_handler.reports]
    assert actions.count("ERROR") == 2


def test_keyboard_stops_on_stop_event(path_tmp: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The coroutine returns promptly once `stop_event` is set, instead of hanging."""

    async def _never_ending_keystrokes(stop_event: asyncio.Event) -> AsyncGenerator[str, None]:
        queue = asyncio.Queue()
        async for ch in stoppable_iterator(queue.get, stop_event):
            yield ch

    monkeypatch.setattr("stepup.core.tui._iter_keystrokes", _never_ending_keystrokes)
    fake_reporter_handler = FakeReporterHandler()
    stop_event = asyncio.Event()
    socket_path = path_tmp / "no-such-socket"

    async def scenario() -> None:
        task = asyncio.create_task(keyboard(socket_path, fake_reporter_handler, stop_event))
        await asyncio.sleep(0.05)
        assert not task.done()
        stop_event.set()
        await task

    asyncio.run(asyncio.wait_for(scenario(), timeout=5))


def test_keyboard_reports_unsupported_key(path_tmp: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An unrecognized key is reported together with the key-stroke help, not dispatched."""
    monkeypatch.setattr("stepup.core.tui._iter_keystrokes", _fixed_keystrokes(["z"]))
    fake_reporter_handler = FakeReporterHandler()
    stop_event = asyncio.Event()
    socket_path = path_tmp / "no-such-socket"
    asyncio.run(
        asyncio.wait_for(keyboard(socket_path, fake_reporter_handler, stop_event), timeout=5)
    )
    assert fake_reporter_handler.reports == [("KEYBOARD", "Unsupported key z")]


def test_async_build_director_spawn_failure_does_not_hang(
    path_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A director that fails to spawn must surface as an error, not hang forever.

    Regression guard: if `stop_event.set()` were the last statement of the `try` block,
    an exception raised earlier (e.g. the director subprocess failing to spawn) would
    leave the reporter RPC server task waiting on `stop_event.wait()` forever, and the
    `finally` block's `asyncio.gather(*tasks)` would never return.
    """
    (path_tmp / "plan.py").touch()

    async def raise_not_found(*args, **kwargs) -> None:
        raise FileNotFoundError("director executable not found")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", raise_not_found)
    args = argparse.Namespace(
        targets=[],
        cgroup=False,
        clean=True,
        duration=True,
        explain_rerun=False,
        keep_going=False,
        jobs=Decimal("1.0"),
        joblog=False,
        fix_epoch=True,
        forkserver=False,
        perf=None,
        preload_modules=None,
        progress=False,
        defer_cap=100,
        resources=None,
        sqllog=False,
        watch=False,
        watch_first=False,
        yappi=False,
        log_level="WARNING",
    )
    with contextlib.chdir(path_tmp), pytest.raises(FileNotFoundError):
        asyncio.run(asyncio.wait_for(_async_build(args), timeout=5))


def test_async_build_stops_reporter_on_nonzero_exit(
    path_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A director exiting with a positive, non-signal code must still stop the reporter.

    Regression guard: calling `reporter_handler.stop_reporting()` only when the director was
    killed by a signal (`returncode < 0`) would leave the `Live` display running and the
    cursor hidden after any other non-clean director exit.
    The call is idempotent, so the exact number of calls is irrelevant.
    """
    (path_tmp / "plan.py").touch()
    fake_reporter_handler = FakeReporterHandler()
    monkeypatch.setattr("stepup.core.tui.ReporterHandler", lambda *a, **kw: fake_reporter_handler)
    fake_process = FakeProcess(returncode=1)

    async def fake_create_subprocess_exec(*args, **kwargs):
        return fake_process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    args = _base_build_args(progress=False)
    with contextlib.chdir(path_tmp):
        returncode = asyncio.run(asyncio.wait_for(_async_build(args), timeout=5))
    assert returncode == 1
    assert fake_reporter_handler.stop_reporting_calls >= 1


def test_async_build_stops_reporter_on_clean_exit(
    path_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A director exiting cleanly still gets `stop_reporting()` called (idempotent no-op)."""
    (path_tmp / "plan.py").touch()
    fake_reporter_handler = FakeReporterHandler()
    monkeypatch.setattr("stepup.core.tui.ReporterHandler", lambda *a, **kw: fake_reporter_handler)
    fake_process = FakeProcess(returncode=0)

    async def fake_create_subprocess_exec(*args, **kwargs):
        return fake_process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    args = _base_build_args(progress=False)
    with contextlib.chdir(path_tmp):
        returncode = asyncio.run(asyncio.wait_for(_async_build(args), timeout=5))
    assert returncode == 0
    assert fake_reporter_handler.stop_reporting_calls >= 1


def test_async_build_stops_reporter_on_spawn_failure(
    path_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An exception on the way to the director must still restore the terminal.

    Regression guard: merely setting `stop_event` in the `finally` block does not stop
    the `Live` display, so an exception raised inside the `try` would leave the cursor
    hidden.
    """
    (path_tmp / "plan.py").touch()
    fake_reporter_handler = FakeReporterHandler()
    monkeypatch.setattr("stepup.core.tui.ReporterHandler", lambda *a, **kw: fake_reporter_handler)

    async def raise_not_found(*args, **kwargs) -> None:
        raise FileNotFoundError("director executable not found")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", raise_not_found)
    args = _base_build_args(progress=False)
    with contextlib.chdir(path_tmp), pytest.raises(FileNotFoundError):
        asyncio.run(asyncio.wait_for(_async_build(args), timeout=5))
    assert fake_reporter_handler.stop_reporting_calls >= 1


@pytest.fixture
def tty_stdin(monkeypatch: pytest.MonkeyPatch) -> Generator[int]:
    """Replace `sys.stdin` with a real pseudo terminal, and yield the primary fd.

    A plain `isatty` patch is not enough for an interactive `_supervise_director`:
    it also reconfigures the terminal, which needs a real `fileno()`.
    """
    primary_fd, secondary_fd = pty.openpty()
    stdin = os.fdopen(secondary_fd, "r")
    monkeypatch.setattr(sys, "stdin", stdin)
    yield primary_fd
    stdin.close()
    os.close(primary_fd)


def _patch_interactive_director(
    monkeypatch: pytest.MonkeyPatch, *, returncode: int, create_socket: bool
) -> tuple[FakeReporterHandler, list[Path]]:
    """Set up `_async_build` for an interactive run against a fake director.

    The caller must also request the `tty_stdin` fixture, which supplies the terminal.

    Parameters
    ----------
    monkeypatch
        The fixture used to patch `_async_build`'s collaborators.
    returncode
        The exit code of the fake director subprocess.
    create_socket
        Whether the fake director creates its socket before exiting.

    Returns
    -------
    fake_reporter_handler, keyboard_calls
        The stand-in reporter handler,
        and the list to which every started `keyboard` task appends its socket path.
    """
    fake_reporter_handler = FakeReporterHandler()
    monkeypatch.setattr("stepup.core.tui.ReporterHandler", lambda *a, **kw: fake_reporter_handler)

    async def fake_create_subprocess_exec(*argv, **kwargs):
        if create_socket:
            # The director socket path is the only argv entry ending in "/director".
            socket_path = next(Path(arg) for arg in argv if str(arg).endswith("/director"))
            socket_path.touch()
        return FakeProcess(returncode=returncode)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    keyboard_calls = []

    async def fake_keyboard(director_socket_path, reporter_handler, stop_event) -> None:
        keyboard_calls.append(director_socket_path)

    monkeypatch.setattr("stepup.core.tui.keyboard", fake_keyboard)
    return fake_reporter_handler, keyboard_calls


def test_async_build_director_exits_before_socket(
    path_tmp: Path, monkeypatch: pytest.MonkeyPatch, tty_stdin: int
) -> None:
    """A director exiting before creating its socket must not hang the TUI.

    Regression test: on a terminal, `_async_build` waited for the director socket with
    `wait_for_path`, which only returns when the path appears or `stop_event` is set.
    Neither can happen once the director is gone, so `stepup build` hung forever and
    could only be recovered with a `SIGKILL` from another terminal.
    An example is a wrapper that refuses to start the director, e.g. `--perf=abc`.
    """
    (path_tmp / "plan.py").touch()
    fake_reporter_handler, keyboard_calls = _patch_interactive_director(
        monkeypatch, returncode=1, create_socket=False
    )
    args = _base_build_args(progress=False)
    with contextlib.chdir(path_tmp):
        returncode = asyncio.run(asyncio.wait_for(_async_build(args), timeout=5))
    assert returncode == 1
    # There is nothing left to send keystrokes to.
    assert keyboard_calls == []
    assert fake_reporter_handler.stop_reporting_calls >= 1


def test_async_build_starts_keyboard_when_socket_appears(
    path_tmp: Path, monkeypatch: pytest.MonkeyPatch, tty_stdin: int
) -> None:
    """On a terminal, the keyboard task is started once the director socket exists."""
    (path_tmp / "plan.py").touch()
    _, keyboard_calls = _patch_interactive_director(monkeypatch, returncode=0, create_socket=True)
    args = _base_build_args(progress=False)
    with contextlib.chdir(path_tmp):
        returncode = asyncio.run(asyncio.wait_for(_async_build(args), timeout=5))
    assert returncode == 0
    assert len(keyboard_calls) == 1
    assert keyboard_calls[0].name == "director"


def test_raw_terminal_restores_attributes() -> None:
    """`RawTerminal` clears `ICANON`/`ECHO` inside the context and restores them after."""
    primary_fd, secondary_fd = pty.openpty()
    try:
        before = termios.tcgetattr(secondary_fd)
        with RawTerminal(secondary_fd):
            inside = termios.tcgetattr(secondary_fd)
            assert not inside[3] & termios.ICANON
            assert not inside[3] & termios.ECHO
        after = termios.tcgetattr(secondary_fd)
        assert after == before
    finally:
        os.close(primary_fd)
        os.close(secondary_fd)


def test_raw_terminal_restores_on_exception() -> None:
    """`RawTerminal` restores the original attributes even when the body raises."""
    primary_fd, secondary_fd = pty.openpty()
    try:
        before = termios.tcgetattr(secondary_fd)
        with pytest.raises(ValueError, match="boom"), RawTerminal(secondary_fd):
            raise ValueError("boom")
        after = termios.tcgetattr(secondary_fd)
        assert after == before
    finally:
        os.close(primary_fd)
        os.close(secondary_fd)


def test_raw_terminal_suspend_resume_round_trip() -> None:
    """`suspend` hands the original attributes back, `resume` takes raw mode over again."""
    primary_fd, secondary_fd = pty.openpty()
    try:
        before = termios.tcgetattr(secondary_fd)
        raw_terminal = RawTerminal(secondary_fd)
        raw_terminal.enter()
        raw_terminal.suspend()
        assert termios.tcgetattr(secondary_fd) == before
        raw_terminal.resume()
        resumed = termios.tcgetattr(secondary_fd)
        assert not resumed[3] & termios.ICANON
        assert not resumed[3] & termios.ECHO
        raw_terminal.leave()
        assert termios.tcgetattr(secondary_fd) == before
    finally:
        os.close(primary_fd)
        os.close(secondary_fd)


def test_raw_terminal_suspend_resume_without_enter() -> None:
    """A `RawTerminal` that never entered raw mode leaves the terminal alone."""
    primary_fd, secondary_fd = pty.openpty()
    try:
        before = termios.tcgetattr(secondary_fd)
        raw_terminal = RawTerminal(secondary_fd)
        raw_terminal.suspend()
        raw_terminal.resume()
        raw_terminal.leave()
        assert termios.tcgetattr(secondary_fd) == before
    finally:
        os.close(primary_fd)
        os.close(secondary_fd)


def test_iter_keystrokes_yields_from_pty(monkeypatch: pytest.MonkeyPatch) -> None:
    """`_iter_keystrokes` reads real keystrokes from a pty put in raw mode by its caller.

    The keystrokes are written to the master only after `RawTerminal` has switched the
    slave into raw mode: it uses `TCSAFLUSH`, which discards unread input still sitting in
    the line discipline, so bytes written while the pty is still in its default canonical
    mode would otherwise be silently dropped (canonical mode also withholds them from
    `read()` until a newline arrives).

    Neither fd is closed at the end: `_iter_keystrokes`' background reader thread stays
    blocked in `sys.stdin.read(1)` after the generator closes (see its own docstring), and
    closing the master out from under it, unlike the real process exit that normally ends
    it, turns that blocking read into an `OSError` raised on a daemon thread pytest has no
    test to attribute it to.
    """
    primary_fd, secondary_fd = pty.openpty()
    before = termios.tcgetattr(secondary_fd)
    monkeypatch.setattr(sys, "stdin", os.fdopen(secondary_fd, "r"))

    async def scenario() -> list[str]:
        stop_event = asyncio.Event()
        chars = []
        async for ch in _iter_keystrokes(stop_event):
            chars.append(ch)
            if len(chars) == 2:
                stop_event.set()
        return chars

    async def run() -> list[str]:
        task = asyncio.create_task(scenario())
        await asyncio.sleep(0.2)
        os.write(primary_fd, b"rq")
        return await task

    with RawTerminal(secondary_fd):
        chars = asyncio.run(asyncio.wait_for(run(), timeout=5))
    assert chars == ["r", "q"]
    assert termios.tcgetattr(secondary_fd) == before
