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
"""Resource-usage accounting for the director process and its children.

Aggregates CPU time, block-IO operation counts, and peak cgroup memory across the director
process, step-executed subprocess/forkserver children, and file-hashing subprocess/forkserver
children, for a compact summary report printed at director shutdown.
"""

import asyncio
import contextlib
import logging
import os
import resource
import sys
import time

import attrs
from path import Path

__all__ = (
    "CgroupMemorySampler",
    "ChildOutcome",
    "ResourceAccumulator",
    "ResourceUsage",
    "find_own_memory_cgroup",
    "format_resource_usage",
)


logger = logging.getLogger(__name__)


@attrs.define(frozen=True)
class ResourceUsage:
    """CPU time and block-IO op counts consumed by one process (or a process + its children)."""

    utime: float = attrs.field(default=0.0)
    """User CPU time [s]."""

    stime: float = attrs.field(default=0.0)
    """System CPU time [s]."""

    inblock: int = attrs.field(default=0)
    """Block input operations."""

    oublock: int = attrs.field(default=0)
    """Block output operations."""

    @classmethod
    def from_rusage_diff(
        cls, ru_self_start, ru_self_end, ru_children_start=None, ru_children_end=None
    ) -> "ResourceUsage":
        """Build from before/after `resource.getrusage()` snapshots.

        Parameters
        ----------
        ru_self_start, ru_self_end
            `resource.getrusage(resource.RUSAGE_SELF)` snapshots taken before and after
            the measured work.
        ru_children_start, ru_children_end
            `resource.getrusage(resource.RUSAGE_CHILDREN)` snapshots taken before and after
            the measured work. Pass both (or neither): omit for pure-Python work that never
            spawns subprocesses, pass when the measured work may spawn subprocess children.

        Returns
        -------
        usage
            The resource usage consumed between the two snapshots.
        """
        utime = ru_self_end.ru_utime - ru_self_start.ru_utime
        stime = ru_self_end.ru_stime - ru_self_start.ru_stime
        inblock = ru_self_end.ru_inblock - ru_self_start.ru_inblock
        oublock = ru_self_end.ru_oublock - ru_self_start.ru_oublock
        if ru_children_start is not None and ru_children_end is not None:
            utime += ru_children_end.ru_utime - ru_children_start.ru_utime
            stime += ru_children_end.ru_stime - ru_children_start.ru_stime
            inblock += ru_children_end.ru_inblock - ru_children_start.ru_inblock
            oublock += ru_children_end.ru_oublock - ru_children_start.ru_oublock
        return cls(utime=utime, stime=stime, inblock=inblock, oublock=oublock)


@attrs.define(frozen=True)
class ChildOutcome:
    """What a child (subprocess or forkserver) produced, plus the resources it used."""

    payload: object = attrs.field()
    """The result on success, or the raised exception on failure.

    For command execution this is a `(returncode, stdout, stderr)` tuple;
    for hashing (`hash_fork_entry`) this is a `HashResult`.
    Callers that want to propagate a failure as an exception
    check `isinstance(payload, BaseException)`.
    """

    usage: ResourceUsage = attrs.field()
    """The CPU time and block-IO ops consumed while producing `payload`."""


@attrs.define
class ResourceAccumulator:
    """Running totals of CPU time and block-IO op counts for child processes."""

    utime: float = attrs.field(init=False, default=0.0)
    """Total accumulated user CPU time [s]."""

    stime: float = attrs.field(init=False, default=0.0)
    """Total accumulated system CPU time [s]."""

    inblock: int = attrs.field(init=False, default=0)
    """Total accumulated block input operations."""

    oublock: int = attrs.field(init=False, default=0)
    """Total accumulated block output operations."""

    def add(self, utime: float, stime: float, inblock: int, oublock: int) -> None:
        """Add one child process's resource usage to the running totals."""
        self.utime += utime
        self.stime += stime
        self.inblock += inblock
        self.oublock += oublock

    def add_usage(self, usage: ResourceUsage) -> None:
        """Add one child process's resource usage (as a `ResourceUsage`) to the running totals."""
        self.add(usage.utime, usage.stime, usage.inblock, usage.oublock)


def _own_cgroup_path() -> Path:
    """Return this process's cgroup v2 path, relative to the cgroup mount.

    Parses `/proc/self/cgroup` for the unified-hierarchy line (`"0::/path"`).
    Raises on any other layout (e.g. a cgroup v1 system, where that file
    has one line per legacy controller instead) or if the file cannot be read.

    The kernel always writes `path` with a leading slash, but it is relative to
    the cgroup v2 mount point, not an absolute filesystem path.
    The leading slash is stripped here so callers can safely join it onto `cgroup_root`.

    Raises
    ------
    OSError
        If `/proc/self/cgroup` cannot be read.
    RuntimeError
        If the file is read but does not contain a unified-hierarchy line.
    """
    with open("/proc/self/cgroup") as fh:
        for line in fh:
            if line.startswith("0::"):
                return Path(line[3:].strip().lstrip("/"))
    raise RuntimeError("Cgroups unavailable: no unified-hierarchy line in /proc/self/cgroup.")


def find_own_memory_cgroup(cgroup_root: str = "/sys/fs/cgroup") -> Path:
    """Return this process's cgroup directory, if memory accounting is usable there.

    This does not create or modify any cgroup;
    it is on the caller (see `stepup.core.tui.cgroup_scope_prefix()`)
    to arrange for this process to already be the sole occupant of its own cgroup,
    e.g. by launching it via `systemd-run --scope`.

    Any failure (not Linux, not cgroup v2, memory accounting not active for this cgroup, ...)
    will raise an exception.

    Parameters
    ----------
    cgroup_root
        The cgroup v2 mount point. Overridable so tests can point this at a fake
        tree instead of the real `/sys/fs/cgroup`.

    Returns
    -------
    cgroup_dir
        The absolute path of this process's own cgroup.

    Raises
    ------
    RuntimeError
        If cgroup memory accounting is unavailable for any reason.
    """
    if sys.platform != "linux":
        raise RuntimeError("Cgroups unavailable: not running on Linux.")
    # Try to get the cgroup path.
    cgroup_root = Path(cgroup_root)
    try:
        own_path = _own_cgroup_path()
    except OSError as exc:
        raise RuntimeError("Cgroups unavailable: failed to read /proc/self/cgroup.") from exc
    # Verify that the director is alone in the cgroup.
    own_dir = cgroup_root / own_path
    try:
        with open(own_dir / "cgroup.procs") as fh:
            pids = [int(line) for line in fh if line.strip()]
    except (OSError, ValueError) as exc:
        raise RuntimeError("Cgroups unavailable: failed to read cgroup.procs.") from exc
    if pids != [os.getpid()]:
        raise RuntimeError("Cgroups unavailable: director is not alone in its cgroup.")
    # Verify that memory accounting is actually active for this cgroup.
    try:
        with open(own_dir / "memory.current") as fh:
            fh.read()
    except (OSError, ValueError) as exc:
        raise RuntimeError("Cgroups unavailable: failed to read memory.current.") from exc
    logger.info("Cgroup memory accounting enabled: sampling memory.current/peak in %s.", own_dir)
    return own_dir


@attrs.define
class CgroupMemorySampler:
    """Periodically sample aggregate cgroup memory across the director's process tree.

    Tracks the peak aggregate memory observed across the director process and every
    process descending from it (step/hash subprocess children, forkserver children,
    and any further processes those in turn spawn). This relies on `cgroup_dir` being
    a cgroup dedicated to that process tree (see `find_own_memory_cgroup()`), so that
    `memory.current`/`memory.peak` are not polluted by unrelated processes and every
    descendant is covered automatically, including short-lived ones and grandchildren
    a step's own command might spawn, without any cooperation from the executor.

    Note that the sampling loop is only needed for kernels older than 5.19,
    where `memory.peak` is not available.
    """

    cgroup_dir: Path | None = attrs.field(default=None)
    """The dedicated cgroup to sample.

    Pass `None` (the default) to auto-detect via `find_own_memory_cgroup()`
    during `__attrs_post_init__`, which raises `RuntimeError` if unavailable.
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
    time_start: float,
    step_accumulator: ResourceAccumulator,
    hash_accumulator: ResourceAccumulator,
    memory_sampler: CgroupMemorySampler | None,
) -> str:
    """Format a resource usage report as a table-like multi-line string for stderr."""
    # `ru_maxrss` is reported in kilobytes on Linux, but in bytes on macOS.
    ru_self = resource.getrusage(resource.RUSAGE_SELF)
    director_maxrss_mib = (
        ru_self.ru_maxrss / 1024 if sys.platform == "linux" else ru_self.ru_maxrss / 1048576
    )

    result = REPORT_TEMPLATE.format(
        wall_time=time.perf_counter() - time_start,
        director_utime=ru_self.ru_utime,
        director_stime=ru_self.ru_stime,
        step_utime=step_accumulator.utime,
        step_stime=step_accumulator.stime,
        hash_utime=hash_accumulator.utime,
        hash_stime=hash_accumulator.stime,
        director_block_io=f"{ru_self.ru_inblock} / {ru_self.ru_oublock}",
        step_block_io=f"{step_accumulator.inblock} / {step_accumulator.oublock}",
        hash_block_io=f"{hash_accumulator.inblock} / {hash_accumulator.oublock}",
        director_mib=director_maxrss_mib,
    )
    if memory_sampler is not None:
        memory_sampler.sample_once()
        if memory_sampler.peak_mib is not None:
            result += "\n" + CGROUP_PEAK_MEM.format(aggregate_mib=memory_sampler.peak_mib)
            return result
    result += "\n" + CGROUP_UNAVAILABLE
    return result


REPORT_TEMPLATE = """\
────────────────────────────────────────────────────────────
RESOURCE USAGE SUMMARY
────────────────────────────────────────────────────────────
Times in seconds                 user         sys       wall
  Elapsed                           -           - {wall_time:10.3f}
  Director                 {director_utime:10.3f}  {director_stime:10.3f}          -
  Steps                    {step_utime:10.3f}  {step_stime:10.3f}          -
  Hashing                  {hash_utime:10.3f}  {hash_stime:10.3f}          -
────────────────────────────────────────────────────────────
Director Blocked I/O ops (In / Out) {director_block_io:>24}
Steps Blocked I/O ops (In / Out)    {step_block_io:>24}
Hashing Blocked I/O ops (In / Out)  {hash_block_io:>24}
────────────────────────────────────────────────────────────
Director Peak Memory (incl. shared libs)       {director_mib:9.1f} MiB"""

CGROUP_PEAK_MEM = """\
Director + Children Peak Memory (cgroup)       {aggregate_mib:9.1f} MiB"""

CGROUP_UNAVAILABLE = """\
Director + Children Peak Memory (cgroup)         unavailable"""
