# StepUp Core provides the basic framework for the StepUp build tool.
# Copyright 2024-2026 Toon Verstraelen
#
# This file is part of StepUp Core.
#
# StepUp Core is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 3
# of the License, or (at your option) any later version.
#
# StepUp Core is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, see <http://www.gnu.org/licenses/>
#
# --
"""Unit tests for stepup.core.usage"""

from types import SimpleNamespace

import pytest

from stepup.core.usage import (
    CgroupMemorySampler,
    ChildOutcome,
    ResourceAccumulator,
    ResourceUsage,
)


def test_resource_accumulator_add():
    acc = ResourceAccumulator()
    acc.add(1.5, 0.5, 3, 4)
    acc.add(2.5, 1.5, 5, 6)
    assert acc.utime == pytest.approx(4.0)
    assert acc.stime == pytest.approx(2.0)
    assert acc.inblock == 8
    assert acc.oublock == 10


def test_resource_usage_defaults():
    usage_ = ResourceUsage()
    assert usage_.utime == 0.0
    assert usage_.stime == 0.0
    assert usage_.inblock == 0
    assert usage_.oublock == 0


def test_resource_usage_from_rusage_diff_self_only():
    ru_start = SimpleNamespace(ru_utime=1.0, ru_stime=2.0, ru_inblock=3, ru_oublock=4)
    ru_end = SimpleNamespace(ru_utime=1.5, ru_stime=2.25, ru_inblock=5, ru_oublock=9)
    usage_ = ResourceUsage.from_rusage_diff(ru_start, ru_end)
    assert usage_.utime == pytest.approx(0.5)
    assert usage_.stime == pytest.approx(0.25)
    assert usage_.inblock == 2
    assert usage_.oublock == 5


def test_resource_usage_from_rusage_diff_self_and_children():
    ru_self_start = SimpleNamespace(ru_utime=1.0, ru_stime=2.0, ru_inblock=3, ru_oublock=4)
    ru_self_end = SimpleNamespace(ru_utime=1.5, ru_stime=2.25, ru_inblock=5, ru_oublock=9)
    ru_children_start = SimpleNamespace(ru_utime=0.1, ru_stime=0.2, ru_inblock=1, ru_oublock=1)
    ru_children_end = SimpleNamespace(ru_utime=0.4, ru_stime=0.5, ru_inblock=4, ru_oublock=2)
    usage_ = ResourceUsage.from_rusage_diff(
        ru_self_start, ru_self_end, ru_children_start, ru_children_end
    )
    # self diff (0.5, 0.25, 2, 5) + children diff (0.3, 0.3, 3, 1)
    assert usage_.utime == pytest.approx(0.8)
    assert usage_.stime == pytest.approx(0.55)
    assert usage_.inblock == 5
    assert usage_.oublock == 6


def test_child_outcome_fields():
    usage_ = ResourceUsage(utime=1.0, stime=0.5, inblock=2, oublock=3)
    outcome = ChildOutcome(payload=("ok", 42), usage=usage_)
    assert outcome.payload == ("ok", 42)
    assert outcome.usage is usage_


def test_resource_accumulator_add_usage():
    acc = ResourceAccumulator()
    acc.add_usage(ResourceUsage(utime=1.5, stime=0.5, inblock=3, oublock=4))
    acc.add_usage(ResourceUsage(utime=2.5, stime=1.5, inblock=5, oublock=6))
    assert acc.utime == pytest.approx(4.0)
    assert acc.stime == pytest.approx(2.0)
    assert acc.inblock == 8
    assert acc.oublock == 10


def test_cgroup_memory_sampler_tracks_peak(path_tmp):
    (path_tmp / "memory.current").write_text("1048576")  # 1 MiB
    sampler = CgroupMemorySampler(cgroup_dir=path_tmp)
    sampler.sample_once()
    assert sampler.nsample == 1
    assert sampler.peak_mib == 1.0

    (path_tmp / "memory.current").write_text("2097152")  # 2 MiB
    sampler.sample_once()
    assert sampler.nsample == 2
    assert sampler.peak_mib == 2.0

    (path_tmp / "memory.current").write_text("524288")  # 0.5 MiB: peak must not drop
    sampler.sample_once()
    assert sampler.nsample == 3
    assert sampler.peak_mib == 2.0


def test_cgroup_memory_sampler_uses_memory_peak_file(path_tmp):
    (path_tmp / "memory.current").write_text("1048576")  # 1 MiB
    (path_tmp / "memory.peak").write_text("5242880")  # 5 MiB kernel-tracked peak
    sampler = CgroupMemorySampler(cgroup_dir=path_tmp)
    sampler.sample_once()
    assert sampler.peak_mib == 5.0
