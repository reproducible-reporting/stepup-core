# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Unit tests for stepup.core.usage"""

from types import SimpleNamespace

import pytest

from stepup.core.outcome import ResourceUsage
from stepup.core.usage import CgroupMemorySampler, ResourceAccumulator


def test_resource_accumulator_add():
    acc = ResourceAccumulator()
    acc.add(1.5, 0.5)
    acc.add(2.5, 1.5)
    assert acc.utime == pytest.approx(4.0)
    assert acc.stime == pytest.approx(2.0)


def test_resource_usage_defaults():
    usage_ = ResourceUsage()
    assert usage_.utime == 0.0
    assert usage_.stime == 0.0


def test_resource_usage_from_diff_self_and_children():
    ru_self_start = SimpleNamespace(ru_utime=1.0, ru_stime=2.0)
    ru_self_end = SimpleNamespace(ru_utime=1.5, ru_stime=2.25)
    ru_children_start = SimpleNamespace(ru_utime=0.1, ru_stime=0.2)
    ru_children_end = SimpleNamespace(ru_utime=0.4, ru_stime=0.5)
    wtime_start = 1.5
    wtime_end = 2.0
    usage_ = ResourceUsage.from_diff(
        ru_self_start, ru_self_end, ru_children_start, ru_children_end, wtime_start, wtime_end
    )
    # self diff (0.5, 0.25) + children diff (0.3, 0.3)
    assert usage_.utime == pytest.approx(0.8)
    assert usage_.stime == pytest.approx(0.55)
    assert usage_.wtime == pytest.approx(0.5)


def test_resource_accumulator_add_usage():
    acc = ResourceAccumulator()
    acc.add_usage(ResourceUsage(utime=1.5, stime=0.5))
    acc.add_usage(ResourceUsage(utime=2.5, stime=1.5))
    assert acc.utime == pytest.approx(4.0)
    assert acc.stime == pytest.approx(2.0)


def test_cgroup_memory_sampler_tracks_peak(path_tmp):
    (path_tmp / "memory.current").write_text("1048576")  # 1 MiB
    sampler = CgroupMemorySampler(cgroup_dir=path_tmp)
    sampler.sample_once()
    assert sampler.peak_mib == 1.0

    (path_tmp / "memory.current").write_text("2097152")  # 2 MiB
    sampler.sample_once()
    assert sampler.peak_mib == 2.0

    (path_tmp / "memory.current").write_text("524288")  # 0.5 MiB: peak must not drop
    sampler.sample_once()
    assert sampler.peak_mib == 2.0


def test_cgroup_memory_sampler_uses_memory_peak_file(path_tmp):
    (path_tmp / "memory.current").write_text("1048576")  # 1 MiB
    (path_tmp / "memory.peak").write_text("5242880")  # 5 MiB kernel-tracked peak
    sampler = CgroupMemorySampler(cgroup_dir=path_tmp)
    sampler.sample_once()
    assert sampler.peak_mib == 5.0
