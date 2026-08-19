# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Outcome and resource usage of a child process."""

import attrs

__all__ = (
    "ChildOutcome",
    "ResourceUsage",
)


@attrs.define(frozen=True)
class ResourceUsage:
    """CPU and wall time consumed by one process (or a process and its children)."""

    utime: float = attrs.field(default=0.0)
    """User CPU time [s]."""

    stime: float = attrs.field(default=0.0)
    """System CPU time [s]."""

    wtime: float = attrs.field(default=0.0)
    """Wall-clock time [s]."""

    def __add__(self, other: "ResourceUsage") -> "ResourceUsage":
        """Combine the resource usage of two processes.

        The CPU times are added up, while the wall times are combined with `max`,
        because the two processes may have run concurrently,
        in which case the sum of their wall times is not the duration of anything.

        Returns
        -------
        combined
            The combined resource usage.
        """
        return ResourceUsage(
            utime=self.utime + other.utime,
            stime=self.stime + other.stime,
            wtime=max(self.wtime, other.wtime),
        )

    @classmethod
    def from_diff(
        cls,
        ru_self_start,
        ru_self_end,
        ru_children_start,
        ru_children_end,
        wtime_start: float,
        wtime_end: float,
    ) -> "ResourceUsage":
        """Build from before/after `resource.getrusage()` snapshots.

        Parameters
        ----------
        ru_self_start, ru_self_end
            `resource.getrusage(resource.RUSAGE_SELF)` snapshots
            taken before and after the measured work.
        ru_children_start, ru_children_end
            `resource.getrusage(resource.RUSAGE_CHILDREN)` snapshots
            taken before and after the measured work.
        wtime_start, wtime_end
            Wall-clock time snapshots taken before and after the measured work.

        Returns
        -------
        usage
            The resource usage consumed between the two snapshots.
        """
        utime = ru_self_end.ru_utime - ru_self_start.ru_utime
        stime = ru_self_end.ru_stime - ru_self_start.ru_stime
        utime += ru_children_end.ru_utime - ru_children_start.ru_utime
        stime += ru_children_end.ru_stime - ru_children_start.ru_stime
        wtime = wtime_end - wtime_start
        return cls(utime=utime, stime=stime, wtime=wtime)


@attrs.define(frozen=True)
class ChildOutcome:
    """What a child (subprocess or forkserver) produced, plus the resources it used."""

    returncode: int = attrs.field()
    """The return code of the child process."""

    stdout: str = attrs.field()
    """The standard output of the child process, decoded to `str`."""

    stderr: str = attrs.field()
    """The standard error of the child process, decoded to `str`."""

    usage: ResourceUsage = attrs.field(factory=ResourceUsage)
    """The resource usage of the child process."""
