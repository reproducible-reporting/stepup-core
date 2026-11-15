# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Unit tests for `stepup.core.platform.stdio`."""

import io
import os
import sys

import pytest
from rich.console import Console

from stepup.core.path import Path
from stepup.core.platform.stdio import is_terminal, rich_legacy_windows, write_utf8_unless_terminal


def _legacy_stream(errors: str) -> io.TextIOWrapper:
    """Create a text stream as Python sets it up for redirected output on a legacy code page."""
    return io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors=errors)


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
    monkeypatch.setattr("stepup.core.platform.stdio.is_terminal", lambda stream: True)
    write_utf8_unless_terminal()
    assert stdout.encoding == "cp1252"


def test_write_utf8_unless_terminal_leaves_other_streams_alone(monkeypatch: pytest.MonkeyPatch):
    """A replaced or missing standard stream cannot be reconfigured, and needs no encoding."""
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    monkeypatch.setattr(sys, "stderr", None)
    write_utf8_unless_terminal()
