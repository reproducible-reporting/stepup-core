# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Unit tests for stepup.core.outcome"""

from types import SimpleNamespace

import pytest

from stepup.core.outcome import ResourceUsage


def test_resource_usage_defaults():
    usage = ResourceUsage()
    assert usage.utime == 0.0
    assert usage.stime == 0.0
    assert usage.wtime == 0.0


def test_resource_usage_add():
    usage = ResourceUsage() + ResourceUsage(utime=1.5, stime=0.5, wtime=3.0)
    usage += ResourceUsage(utime=2.5, stime=1.5, wtime=2.0)
    assert usage.utime == pytest.approx(4.0)
    assert usage.stime == pytest.approx(2.0)
    # Wall times are combined with max, not summed.
    assert usage.wtime == pytest.approx(3.0)


def test_resource_usage_from_diff_self_and_children():
    ru_self_start = SimpleNamespace(ru_utime=1.0, ru_stime=2.0)
    ru_self_end = SimpleNamespace(ru_utime=1.5, ru_stime=2.25)
    ru_children_start = SimpleNamespace(ru_utime=0.1, ru_stime=0.2)
    ru_children_end = SimpleNamespace(ru_utime=0.4, ru_stime=0.5)
    wtime_start = 1.5
    wtime_end = 2.0
    usage = ResourceUsage.from_diff(
        ru_self_start, ru_self_end, ru_children_start, ru_children_end, wtime_start, wtime_end
    )
    # self diff (0.5, 0.25) + children diff (0.3, 0.3)
    assert usage.utime == pytest.approx(0.8)
    assert usage.stime == pytest.approx(0.55)
    assert usage.wtime == pytest.approx(0.5)
