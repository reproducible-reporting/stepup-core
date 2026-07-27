# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Resource-usage accounting for the director process and its children.

Aggregates CPU time and peak cgroup memory across the director process
and step-executed subprocess/forkserver children,
for a compact summary report printed at director shutdown.
"""

import asyncio
import contextlib
import resource
import sys
import time

import attrs
from path import Path

from .cgroups import find_own_memory_cgroup
from .outcome import ResourceUsage

__all__ = (
    "CgroupMemorySampler",
    "ResourceAccumulator",
    "format_resource_usage",
)


@attrs.define
class ResourceAccumulator:
    """Running totals of CPU time for child processes."""

    utime: float = attrs.field(init=False, default=0.0)
    """Total accumulated user CPU time [s]."""

    stime: float = attrs.field(init=False, default=0.0)
    """Total accumulated system CPU time [s]."""

    def add(self, utime: float, stime: float) -> None:
        """Add one child process's resource usage to the running totals."""
        self.utime += utime
        self.stime += stime

    def add_usage(self, usage: ResourceUsage) -> None:
        """Add one child process's resource usage (as a `ResourceUsage`) to the running totals."""
        self.add(usage.utime, usage.stime)


@attrs.define
class CgroupMemorySampler:
    """Periodically sample aggregate cgroup memory across the director's process tree.

    Tracks the peak aggregate memory observed across the director process
    and every process descending from it
    (step subprocess children, forkserver children, and any further processes those in turn spawn).
    This relies on `cgroup_dir` being a cgroup dedicated to that process tree
    (see `find_own_memory_cgroup()`), so that `memory.current`/`memory.peak` are not polluted
    by unrelated processes and every descendant is covered automatically,
    including short-lived ones and grandchildren a step's own command might spawn,
    without any cooperation from the executor.

    Note that the sampling loop is only needed for kernels older than 5.19,
    where `memory.peak` is not available.
    """

    cgroup_dir: Path | None = attrs.field(default=None)
    """The dedicated cgroup to sample.

    Pass `None` (the default) to auto-detect via `find_own_memory_cgroup()`
    during `__attrs_post_init__`, which raises `CgroupError` if unavailable.
    """

    interval: float = attrs.field(default=1.0)
    """Sampling period [s]."""

    peak_mib: float | None = attrs.field(init=False, default=None)
    """The highest aggregate memory usage observed so far, in mibibytes."""

    nsample: int = attrs.field(init=False, default=0)
    """The number of samples successfully read (0 means no peak is available)."""

    def __attrs_post_init__(self) -> None:
        if self.cgroup_dir is None:
            self.cgroup_dir = find_own_memory_cgroup()

    def sample_once(self) -> None:
        """Take one sample and update `peak_mib` if it is a new maximum.

        Silently skips the sample if neither `memory.peak` nor `memory.current`
        is readable, e.g. due to a race with the cgroup scope being torn down.
        """
        best_mib = None
        with contextlib.suppress(OSError, ValueError), open(self.cgroup_dir / "memory.peak") as fh:
            best_mib = int(fh.read()) / 1048576
        if best_mib is None:
            with (
                contextlib.suppress(OSError, ValueError),
                open(self.cgroup_dir / "memory.current") as fh,
            ):
                best_mib = int(fh.read()) / 1048576
        if best_mib is not None:
            self.nsample += 1
            self.peak_mib = best_mib if self.peak_mib is None else max(self.peak_mib, best_mib)

    async def loop(self, stop_event: asyncio.Event) -> None:
        """Sample memory usage periodically until `stop_event` is set."""
        while not stop_event.is_set():
            try:
                self.sample_once()
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(stop_event.wait(), timeout=self.interval)
                if stop_event.is_set():
                    break
            except asyncio.CancelledError:
                break


def format_resource_usage(
    wtime_start: float,
    step_accumulator: ResourceAccumulator,
    memory_sampler: CgroupMemorySampler | None,
) -> tuple[str, str]:
    """Format a resource usage report as a table-like multi-line string for stderr."""
    # `ru_maxrss` is reported in kilobytes on Linux, but in bytes on macOS.
    ru_self = resource.getrusage(resource.RUSAGE_SELF)
    director_maxrss_mib = (
        ru_self.ru_maxrss / 1024 if sys.platform == "linux" else ru_self.ru_maxrss / 1048576
    )

    wtime = time.perf_counter() - wtime_start
    report = REPORT_TEMPLATE.format(
        wtime=wtime,
        director_utime=ru_self.ru_utime,
        director_stime=ru_self.ru_stime,
        step_utime=step_accumulator.utime,
        step_stime=step_accumulator.stime,
        director_mib=director_maxrss_mib,
    )
    sampler_reported = False
    if memory_sampler is not None:
        memory_sampler.sample_once()
        if memory_sampler.peak_mib is not None:
            report += "\n" + CGROUP_PEAK_MEM.format(aggregate_mib=memory_sampler.peak_mib)
            sampler_reported = True
    if not sampler_reported:
        report += "\n" + CGROUP_UNAVAILABLE
    summary = (
        f"Wall {wtime:.1f}s, Director {ru_self.ru_utime:.1f}u/{ru_self.ru_stime:.1f}s, "
        f"Steps {step_accumulator.utime:.1f}u/{step_accumulator.stime:.1f}s"
    )
    return report, summary


REPORT_TEMPLATE = """\
────────────────────────────────────────────────────────────
RESOURCE USAGE SUMMARY
────────────────────────────────────────────────────────────
Times in seconds                 user         sys       wall
  Elapsed                           -           - {wtime:10.3f}
  Director                 {director_utime:10.3f}  {director_stime:10.3f}          -
  Steps                    {step_utime:10.3f}  {step_stime:10.3f}          -
────────────────────────────────────────────────────────────
Director Peak Memory (incl. shared libs)       {director_mib:9.1f} MiB"""

CGROUP_PEAK_MEM = """\
Director + Children Peak Memory (cgroup)       {aggregate_mib:9.1f} MiB"""

CGROUP_UNAVAILABLE = """\
Director + Children Peak Memory (cgroup)         unavailable"""
