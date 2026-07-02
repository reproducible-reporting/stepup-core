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

Aggregates CPU time, block-IO operation counts, and peak PSS memory across the director
process, step-executed subprocess/forkserver children, and file-hashing subprocess/forkserver
children, for a compact summary report printed at director shutdown.
"""

import asyncio
import contextlib
import os
import resource
import sys
import time

import attrs

__all__ = (
    "PSS_AVAILABLE",
    "ChildOutcome",
    "PssSampler",
    "ResourceAccumulator",
    "ResourceUsage",
    "format_resource_usage",
    "read_pss_kib",
)


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


def _child_pids(pid: int) -> list[int]:
    """Return the immediate child pids of `pid`.

    Reads `/proc/{pid}/task/*/children`, which the kernel maintains per thread.
    Returns an empty list if `pid` is gone or the kernel does not expose this file.
    """
    children: list[int] = []
    try:
        task_dir = f"/proc/{pid}/task"
        tids = os.listdir(task_dir)
    except OSError:
        return children
    for tid in tids:
        try:
            with open(f"{task_dir}/{tid}/children") as fh:
                children.extend(int(child_pid) for child_pid in fh.read().split())
        except OSError:
            continue
    return children


def _descendant_pids(root_pid: int) -> set[int]:
    """Return every pid reachable by recursively following its children, `root_pid` not included.

    This is a best-effort snapshot: a process that forks a new child between the
    moment this walk reads its children and the moment it recurses into them
    will have that new child missed for the current sample.
    """
    pids = set()
    frontier = [root_pid]
    while frontier:
        pid = frontier.pop()
        for child_pid in _child_pids(pid):
            if child_pid not in pids:
                pids.add(child_pid)
                frontier.append(child_pid)
    return pids


PSS_AVAILABLE = sys.platform == "linux"


def read_pss_kib(pid: int) -> int | None:
    """Read the proportional set size (PSS) of a process, in kibibytes.

    PSS apportions shared pages fractionally across the processes sharing them,
    so summing PSS over a set of concurrently alive processes gives a memory total
    that does not double-count shared memory (unlike a plain sum of RSS values).

    Parameters
    ----------
    pid
        The process id to inspect.

    Returns
    -------
    pss_kib
        The PSS in kibibytes, or `None` if unavailable: not on Linux,
        the process is gone, or its `/proc` entry is not readable.
    """
    if not PSS_AVAILABLE:
        return None
    try:
        with open(f"/proc/{pid}/smaps_rollup") as fh:
            for line in fh:
                if line.startswith("Pss:"):
                    return int(line.split()[1])
    except (OSError, ValueError):
        return None
    return None


@attrs.define
class PssSampler:
    """Periodically sample aggregate PSS memory across the director's full process tree.

    Tracks the peak aggregate PSS observed across the director process and every
    process descending from it (step/hash subprocess children, forkserver children,
    and any further processes those in turn spawn), so that short-lived children
    (which no longer exist in `/proc` once they complete) still contribute to the
    peak, as long as they were alive during at least one sample.

    The descendant set is rediscovered from scratch on every sample by recursively
    following `/proc/{pid}/task/*/children` starting at the director's own pid.
    This needs no cooperation from the executor and naturally covers grandchildren
    that a step's own command might spawn (e.g. a shell script forking children of
    its own), unlike sampling only the pids directly tracked by `executor.running`.
    Walking from the director's pid also avoids scanning all of `/proc`, which
    matters on shared machines (e.g. multi-user login nodes) with many unrelated
    processes.
    """

    interval: float = attrs.field(default=1.0)
    """Sampling period [s]."""

    peak_kib: int = attrs.field(init=False, default=0)
    """The highest aggregate PSS observed so far, in kibibytes."""

    nsamples: int = attrs.field(init=False, default=0)
    """The number of samples with at least one readable pid (0 means no peak is available)."""

    def sample_once(self) -> None:
        """Take one sample and update `peak_kib` if it is a new maximum.

        Does nothing when `PSS_AVAILABLE` is `False`.
        """
        if not PSS_AVAILABLE:
            return
        total = 0
        any_read = False
        for pid in _descendant_pids(os.getpid()):
            pss_kib = read_pss_kib(pid)
            if pss_kib is not None:
                total += pss_kib
                any_read = True
        if any_read:
            self.nsamples += 1
            self.peak_kib = max(self.peak_kib, total)

    async def loop(self, stop_event: asyncio.Event) -> None:
        """Background loop: sample immediately, then periodically until `stop_event` is set.

        An eager first sample ensures very short-lived builds still get one data point.
        Each sample runs in a worker thread because it does blocking `/proc` file I/O,
        which would otherwise stall the director's event loop for the sample's duration.
        """
        await asyncio.to_thread(self.sample_once)
        while not stop_event.is_set():
            try:
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(stop_event.wait(), timeout=self.interval)
                if stop_event.is_set():
                    break
                await asyncio.to_thread(self.sample_once)
            except asyncio.CancelledError:
                break


def format_resource_usage(
    time_start: float,
    step_accumulator: ResourceAccumulator,
    hash_accumulator: ResourceAccumulator,
    pss_sampler: PssSampler,
) -> str:
    """Format a resource usage report as a table-like multi-line string for stderr."""
    ru_self = resource.getrusage(resource.RUSAGE_SELF)

    # `ru_maxrss` is reported in kilobytes on Linux, but in bytes on macOS.
    director_maxrss_kib = (
        ru_self.ru_maxrss if sys.platform == "linux" else ru_self.ru_maxrss // 1024
    )
    peak_aggregate_pss_kib = pss_sampler.peak_kib if pss_sampler.nsamples > 0 else None

    return REPORT_TEMPLATE.format(
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
        director_mib=director_maxrss_kib / 1024.0,
        aggregate_mib=(
            peak_aggregate_pss_kib / 1024.0 if peak_aggregate_pss_kib is not None else float("nan")
        ),
    )


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
Director Peak Memory (RSS)                     {director_mib:9.1f} MiB
Children Peak Memory (PSS, sampled)            {aggregate_mib:9.1f} MiB"""
