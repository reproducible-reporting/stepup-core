# SPDX-FileCopyrightText: 2026 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Tests for stepup.core.winapi, which can only run on Windows."""

import os
import subprocess
import sys

import pytest

if sys.platform == "win32":
    from stepup.core.winapi import get_peak_working_set, is_process_alive

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows only")


def test_is_process_alive_self() -> None:
    assert is_process_alive(os.getpid())


def test_is_process_alive_exited_child() -> None:
    """An exited child is not alive, although the `Popen` object still holds a handle to it."""
    with subprocess.Popen([sys.executable, "-c", "input()"], stdin=subprocess.PIPE) as proc:
        assert is_process_alive(proc.pid)
        proc.communicate(b"\n")
        assert not is_process_alive(proc.pid)


def test_is_process_alive_exit_code_259() -> None:
    """A process that exits with `STILL_ACTIVE` (259) is not mistaken for a running one."""
    with subprocess.Popen([sys.executable, "-c", "raise SystemExit(259)"]) as proc:
        assert proc.wait() == 259
        assert not is_process_alive(proc.pid)


def test_get_peak_working_set() -> None:
    peak = get_peak_working_set()
    assert 10 * 1024**2 < peak < 100 * 1024**3
