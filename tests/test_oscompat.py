# SPDX-FileCopyrightText: 2026 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Tests for stepup.core.oscompat, pinning the answers on the platforms StepUp supports."""

import sys

import pytest

from stepup.core.oscompat import (
    forkserver_available,
    perf_available,
    raw_terminal_available,
    suspend_available,
)

ALL_PREDICATES = (forkserver_available, perf_available, raw_terminal_available, suspend_available)


@pytest.mark.skipif(sys.platform != "linux", reason="Linux only")
@pytest.mark.parametrize("predicate", ALL_PREDICATES)
def test_linux_has_everything(predicate) -> None:
    assert predicate()


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")
def test_macos() -> None:
    assert forkserver_available()
    assert not perf_available()
    assert raw_terminal_available()
    assert suspend_available()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
@pytest.mark.parametrize("predicate", ALL_PREDICATES)
def test_windows_has_nothing(predicate) -> None:
    assert not predicate()
