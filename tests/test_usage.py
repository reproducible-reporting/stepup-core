# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Unit tests for stepup.core.usage"""

import asyncio
import time

import pytest
from path import Path

from stepup.core.outcome import ResourceUsage
from stepup.core.usage import (
    CGROUP_NOT_ENABLED_LINE,
    CGROUP_PEAK_MEM_TEMPLATE,
    CGROUP_UNAVAILABLE_LINE,
    REPORT_TEMPLATE,
    CgroupMemorySampler,
    finalize_resource_usage,
)

#
# Cgroup memory sampling
#


def test_cgroup_memory_sampler_tracks_peak(path_tmp: Path):
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


def test_cgroup_memory_sampler_uses_memory_peak_file(path_tmp: Path):
    (path_tmp / "memory.current").write_text("1048576")  # 1 MiB
    (path_tmp / "memory.peak").write_text("5242880")  # 5 MiB kernel-tracked peak
    sampler = CgroupMemorySampler(cgroup_dir=path_tmp)
    sampler.sample_once()
    assert sampler.peak_mib == 5.0


def test_cgroup_memory_sampler_without_readable_files(path_tmp: Path):
    sampler = CgroupMemorySampler(cgroup_dir=path_tmp)
    sampler.sample_once()
    assert sampler.peak_mib is None


async def test_cgroup_memory_sampler_loop_stops(path_tmp: Path):
    (path_tmp / "memory.current").write_text("1048576")  # 1 MiB
    # The interval is long, so the loop can only end quickly through the stop event.
    sampler = CgroupMemorySampler(cgroup_dir=path_tmp, interval=100.0)
    stop_event = asyncio.Event()
    task = asyncio.create_task(sampler.loop(stop_event))
    # Let the loop take its first sample and park on the stop event.
    await asyncio.sleep(0)
    assert sampler.peak_mib == 1.0
    stop_event.set()
    await asyncio.wait_for(task, timeout=5.0)


async def test_cgroup_memory_sampler_loop_cancelled(path_tmp: Path):
    (path_tmp / "memory.current").write_text("1048576")  # 1 MiB
    sampler = CgroupMemorySampler(cgroup_dir=path_tmp, interval=100.0)
    task = asyncio.create_task(sampler.loop(asyncio.Event()))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled()


#
# Reporting
#


def test_report_memory_lines_are_aligned():
    lines = [
        REPORT_TEMPLATE.format(
            wtime=0.0,
            director_utime=0.0,
            director_stime=0.0,
            step_utime=0.0,
            step_stime=0.0,
            director_mib=1.0,
        ).splitlines()[-1],
        CGROUP_PEAK_MEM_TEMPLATE.format(aggregate_mib=1.0),
        CGROUP_NOT_ENABLED_LINE,
        CGROUP_UNAVAILABLE_LINE,
    ]
    assert [len(line) for line in lines] == [60, 60, 60, 60]


def test_finalize_resource_usage_without_sampler():
    step_usage = ResourceUsage(utime=2.0, stime=1.0, wtime=7.0)
    report, summary = finalize_resource_usage(time.perf_counter() - 5.0, step_usage, None)
    assert "RESOURCE USAGE SUMMARY" in report
    assert report.endswith(CGROUP_NOT_ENABLED_LINE)
    assert "  Steps                         2.000       1.000          -" in report
    assert summary.startswith("Wall 5.0s, Director ")
    assert summary.endswith("Steps 2.0u/1.0s")


def test_finalize_resource_usage_takes_final_sample(path_tmp: Path):
    sampler = CgroupMemorySampler(cgroup_dir=path_tmp)
    sampler.sample_once()
    assert sampler.peak_mib is None
    (path_tmp / "memory.current").write_text("3145728")  # 3 MiB
    report, _ = finalize_resource_usage(time.perf_counter(), ResourceUsage(), sampler)
    assert sampler.peak_mib == 3.0
    assert report.endswith(CGROUP_PEAK_MEM_TEMPLATE.format(aggregate_mib=3.0))


def test_finalize_resource_usage_unavailable(path_tmp: Path):
    sampler = CgroupMemorySampler(cgroup_dir=path_tmp)
    report, _ = finalize_resource_usage(time.perf_counter(), ResourceUsage(), sampler)
    assert report.endswith(CGROUP_UNAVAILABLE_LINE)
