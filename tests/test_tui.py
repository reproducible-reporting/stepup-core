# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Tests for stepup.core.tui."""

import argparse
import contextlib

import pytest
from path import Path

from stepup.core.enums import ReturnCode
from stepup.core.exceptions import TUIError
from stepup.core.tui import (
    _normalize_targets,
    _resolve_root_and_targets,
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
