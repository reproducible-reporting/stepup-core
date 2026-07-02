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
    "read_cgroup_memory_mib",
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


def _own_cgroup_path() -> str | None:
    """Return this process's cgroup v2 path, relative to the cgroup mount, or `None`.

    Parses `/proc/self/cgroup` for the unified-hierarchy line (`"0::/path"`).
    Returns `None` on any other layout (e.g. a cgroup v1 system, where that file
    has one line per legacy controller instead) or if the file cannot be read.

    The kernel always writes `path` with a leading slash, but it is relative to
    the cgroup v2 mount point, not an absolute filesystem path — that leading
    slash is stripped here so callers can safely join it onto `cgroup_root`
    (joining two `Path`s where the second looks absolute would otherwise
    silently discard the first).
    """
    try:
        with open("/proc/self/cgroup") as fh:
            for line in fh:
                if line.startswith("0::"):
                    return line[3:].strip().lstrip("/")
    except OSError:
        return None
    return None


def find_own_memory_cgroup(cgroup_root: str = "/sys/fs/cgroup") -> str | None:
    """Return this process's cgroup directory, if memory accounting is usable there.

    Cgroup v2 memory accounting (`memory.current` / `memory.peak`) reflects every
    process in a cgroup, so it only gives a meaningful total for "this process and
    its descendants" if that cgroup does not also contain unrelated processes — which
    is essentially never true for a plain interactive invocation (the shell, and
    everything else in the session, share that cgroup too). This does not create or
    modify any cgroup; it is on the caller (see `stepup.core.tui.cgroup_scope_prefix()`)
    to arrange for this process to already be the sole occupant of its own cgroup, e.g.
    by launching it via `systemd-run --scope`.

    This is best-effort: any failure (not Linux, not cgroup v2, memory accounting not
    active for this cgroup, ...) is logged and reported by returning `None` rather than
    raising, per this module's "no fallback" policy — callers should simply skip memory
    sampling in that case.

    Parameters
    ----------
    cgroup_root
        The cgroup v2 mount point. Overridable so tests can point this at a fake
        tree instead of the real `/sys/fs/cgroup`.

    Returns
    -------
    cgroup_dir
        The absolute path of this process's own cgroup, or `None` if cgroup memory
        accounting is not usable there.
    """
    if sys.platform != "linux":
        logger.info("Cgroup memory accounting unavailable: not running on Linux.")
        return None
    # Try to get the cgroup path.
    cgroup_root = Path(cgroup_root)
    own_path = _own_cgroup_path()
    if own_path is None:
        logger.info("Cgroup memory accounting unavailable: not using cgroup v2.")
        return None
    # Verify that the director is alone in the cgroup.
    own_dir = cgroup_root / own_path
    try:
        with open(own_dir / "cgroup.procs") as fh:
            pids = [int(line) for line in fh if line.strip()]
    except (OSError, ValueError):
        logger.info("Cgroup memory accounting unavailable: failed to read cgroup.procs.")
        return None
    if pids != [os.getpid()]:
        logger.info(
            "Cgroup memory accounting unavailable: cgroup %s contains other processes: %s.",
            own_path,
            pids,
        )
        return None
    # Verify that memory accounting is actually active for this cgroup.
    if read_cgroup_memory_mib(own_dir, "current") is None:
        logger.info(
            "Cgroup memory accounting unavailable: memory.current not readable in %s.", own_dir
        )
        return None
    logger.info("Cgroup memory accounting enabled: sampling memory.current/peak in %s.", own_dir)
    return own_dir


def read_cgroup_memory_mib(cgroup_dir: str, kind: str) -> float | None:
    """Read a cgroup's memory usage, in mibibytes.

    Parameters
    ----------
    cgroup_dir
        Absolute path of the cgroup, as returned by `find_own_memory_cgroup()`.

    Returns
    -------
    memory_mib
        The memory usage in mibibytes, or `None` if unreadable.
    """
    try:
        with open(Path(cgroup_dir) / f"memory.{kind}") as fh:
            return int(fh.read()) / 1048576
    except (OSError, ValueError):
        return None


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

    cgroup_dir: str | None = attrs.field()
    """The dedicated cgroup to sample, or `None` if cgroup memory accounting is unavailable."""

    interval: float = attrs.field(default=1.0)
    """Sampling period [s]."""

    peak_mib: float | None = attrs.field(init=False, default=None)
    """The highest aggregate memory usage observed so far, in mibibytes."""

    nsample: int = attrs.field(init=False, default=0)
    """The number of samples successfully read (0 means no peak is available)."""

    def sample_once(self) -> None:
        """Take one sample and update `peak_mib` if it is a new maximum.

        Does nothing when `cgroup_dir` is `None`.
        """
        if self.cgroup_dir is None:
            return
        current_mib = read_cgroup_memory_mib(self.cgroup_dir, "current")
        peak_mib = read_cgroup_memory_mib(self.cgroup_dir, "peak")
        best_mib = max((mib for mib in (current_mib, peak_mib) if mib is not None), default=None)
        if best_mib is not None:
            self.nsample += 1
            self.peak_mib = best_mib if self.peak_mib is None else max(self.peak_mib, best_mib)

    async def loop(self, stop_event: asyncio.Event) -> None:
        """Background loop: sample immediately, then periodically until `stop_event` is set.

        An eager first sample ensures very short-lived builds still get one data point.
        Unlike `/proc`-tree-walking, a sample here is just one or two small cgroup
        pseudo-file reads, cheap enough to run directly on the event loop.
        """
        self.sample_once()
        while not stop_event.is_set():
            try:
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(stop_event.wait(), timeout=self.interval)
                if stop_event.is_set():
                    break
                self.sample_once()
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
