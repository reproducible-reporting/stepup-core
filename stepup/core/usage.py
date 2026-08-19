# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Resource-usage accounting for the director process and its children.

Samples the peak cgroup memory of the director process
and the subprocess and forkserver children that run steps,
and formats a compact summary report of memory and CPU time at the end of a run.
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
    "finalize_resource_usage",
)

MIB = 1024**2
"""The number of bytes in a mebibyte."""


#
# Cgroup memory sampling
#


@attrs.define
class CgroupMemorySampler:
    """Periodically sample aggregate cgroup memory across the director's process tree.

    Tracks the peak aggregate memory observed across the director process
    and every process descending from it
    (step subprocess children, forkserver children, and any further processes those in turn spawn).
    This relies on `cgroup_dir` being a cgroup dedicated to that process tree
    (see `find_own_memory_cgroup()`),
    so that `memory.current`/`memory.peak` are not polluted by unrelated processes
    and every descendant is covered automatically,
    including short-lived ones and grandchildren a step's own command might spawn,
    without any cooperation from the executor.

    The periodic samples of `loop()` are the only source of a peak
    on kernels older than 5.19, which do not provide `memory.peak`.
    They keep running on newer kernels as well,
    so that a recent reading is available if the final sample fails,
    e.g. because the cgroup scope is being torn down.
    """

    cgroup_dir: Path = attrs.field(factory=find_own_memory_cgroup)
    """The dedicated cgroup to sample.

    Defaults to this process's own cgroup, as detected by `find_own_memory_cgroup()`,
    which raises `CgroupError` if cgroup memory accounting is unavailable.
    """

    interval: float = attrs.field(default=1.0)
    """Sampling period [s]."""

    peak_mib: float | None = attrs.field(init=False, default=None)
    """The highest aggregate memory usage observed so far [MiB]."""

    def _read_mib(self, name: str) -> float | None:
        """Read a memory file of the cgroup [MiB], or return `None` if it is not readable."""
        with contextlib.suppress(OSError, ValueError), open(self.cgroup_dir / name) as fh:
            return int(fh.read()) / MIB
        return None

    def sample_once(self) -> None:
        """Take one sample and update `peak_mib` if it is a new maximum.

        Silently skips the sample if neither `memory.peak` nor `memory.current`
        is readable, e.g. due to a race with the cgroup scope being torn down.
        """
        mib = self._read_mib("memory.peak")
        if mib is None:
            mib = self._read_mib("memory.current")
        if mib is not None and (self.peak_mib is None or mib > self.peak_mib):
            self.peak_mib = mib

    async def loop(self, stop_event: asyncio.Event) -> None:
        """Sample memory usage periodically until `stop_event` is set."""
        while not stop_event.is_set():
            self.sample_once()
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(stop_event.wait(), timeout=self.interval)


#
# Reporting
#

# The memory lines of the report are aligned by hand:
# the label fills the first 40 columns and the value ends at column 60,
# which is also the width of the horizontal rules.

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

CGROUP_PEAK_MEM_TEMPLATE = """\
Director + Children Peak Memory (cgroup)       {aggregate_mib:9.1f} MiB"""

CGROUP_NOT_ENABLED_LINE = """\
Director + Children Peak Memory (cgroup)         not enabled"""

CGROUP_UNAVAILABLE_LINE = """\
Director + Children Peak Memory (cgroup)         unavailable"""

SUMMARY_TEMPLATE = (
    "Wall {wtime:.1f}s, Director {director_utime:.1f}u/{director_stime:.1f}s, "
    "Steps {step_utime:.1f}u/{step_stime:.1f}s"
)


def finalize_resource_usage(
    wtime_start: float,
    step_usage: ResourceUsage,
    memory_sampler: CgroupMemorySampler | None,
) -> tuple[str, str]:
    """Close off the resource-usage measurements and format them as a report.

    This takes the final measurements itself:
    the elapsed wall time, the director's own `getrusage` totals
    and, when a sampler is given, one last memory sample.

    Parameters
    ----------
    wtime_start
        The wall-clock time when the director started, as returned by `time.perf_counter()`.
    step_usage
        The resource usage accumulated over all steps.
    memory_sampler
        The `CgroupMemorySampler` that has been sampling peak cgroup memory usage,
        or `None` if cgroup accounting was not enabled.

    Returns
    -------
    report
        A table-like, multi-line overview of wall time, CPU times and peak memory.
    summary
        A one-line condensation of the same wall and CPU times.
    """
    # `ru_maxrss` is reported in kibibytes on Linux, but in bytes on macOS.
    ru_self = resource.getrusage(resource.RUSAGE_SELF)
    director_mib = ru_self.ru_maxrss / 1024 if sys.platform == "linux" else ru_self.ru_maxrss / MIB

    if memory_sampler is None:
        cgroup_line = CGROUP_NOT_ENABLED_LINE
    else:
        memory_sampler.sample_once()
        cgroup_line = (
            CGROUP_UNAVAILABLE_LINE
            if memory_sampler.peak_mib is None
            else CGROUP_PEAK_MEM_TEMPLATE.format(aggregate_mib=memory_sampler.peak_mib)
        )

    times = {
        "wtime": time.perf_counter() - wtime_start,
        "director_utime": ru_self.ru_utime,
        "director_stime": ru_self.ru_stime,
        "step_utime": step_usage.utime,
        "step_stime": step_usage.stime,
    }
    report = REPORT_TEMPLATE.format(director_mib=director_mib, **times) + "\n" + cgroup_line
    return report, SUMMARY_TEMPLATE.format(**times)
