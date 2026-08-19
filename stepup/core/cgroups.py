# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Shared cgroup v2 helpers, used for both memory accounting and CPU core detection."""

import logging
import math
import os
import shutil
import subprocess
import sys

from path import Path

from .exceptions import CgroupError

__all__ = ("cgroup_scope_prefix", "find_own_memory_cgroup", "get_ncore_from_cgroup")


logger = logging.getLogger(__name__)


def cgroup_scope_prefix() -> list[str]:
    """Return an argv prefix that launches a command in its own `systemd-run --scope` cgroup.

    Returns
    -------
    argv_prefix
        A list of strings that can be prepended to a command
        to launch it in its own `systemd-run --scope` cgroup.

    Raises
    ------
    RuntimeError
        If not running on Linux, if `systemd-run` is not available,
        or if a preflight probe of `systemd-run` fails.
    """
    if sys.platform != "linux":
        raise RuntimeError("Cgroup isolation is only supported on Linux.")
    systemd_run = shutil.which("systemd-run")
    if systemd_run is None:
        raise RuntimeError("systemd-run not available.")
    prefix = [systemd_run, "--user", "--scope", "--quiet", "-p", "Delegate=yes", "--"]
    try:
        subprocess.run(
            [*prefix, "true"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
            timeout=3.0,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("systemd-run probe timed out.") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError("systemd-run probe subprocess failed.") from exc
    except OSError as exc:
        raise RuntimeError(f"systemd-run probe failed with {type(exc).__name__}.") from exc
    return prefix


def _own_cgroup_path() -> Path:
    """Return this process's cgroup v2 path, relative to the cgroup v2 mount point.

    Parses `/proc/self/cgroup` for the unified-hierarchy line (`"0::/path"`).
    The kernel always writes `path` with a leading slash,
    but it is relative to the cgroup v2 mount point, not an absolute file system path.
    The leading slash is stripped here so it can safely be joined onto a cgroup v2 mount point.

    Returns
    -------
    own_path
        The cgroup v2 path of this process, relative to the cgroup v2 mount point.

    Raises
    ------
    OSError
        If `/proc/self/cgroup` cannot be read.
    CgroupError
        If the file is read but does not contain a unified-hierarchy line.
        (e.g. a cgroup v1 system, where that file has one line per legacy controller instead)
    """
    with open("/proc/self/cgroup") as fh:
        for line in fh:
            if line.startswith("0::"):
                return Path(line[3:].strip().lstrip("/"))
    raise CgroupError("Cgroups unavailable: no unified-hierarchy line in /proc/self/cgroup.")


def _own_cgroup_dir(cgroup_root: str) -> Path:
    """Return this process's cgroup v2 directory.

    Parameters
    ----------
    cgroup_root
        The cgroup v2 mount point.
        Overridable so tests can point this at a fake tree instead of the real `/sys/fs/cgroup`.

    Returns
    -------
    own_dir
        The absolute path of this process's own cgroup.

    Raises
    ------
    CgroupError
        If not running on Linux,
        or if `/proc/self/cgroup` cannot be read or has no unified-hierarchy line.
    """
    if sys.platform != "linux":
        raise CgroupError("Cgroups unavailable: not running on Linux.")
    try:
        own_path = _own_cgroup_path()
    except OSError as exc:
        raise CgroupError("Cgroups unavailable: failed to read /proc/self/cgroup.") from exc
    return Path(cgroup_root) / own_path


def find_own_memory_cgroup(cgroup_root: str = "/sys/fs/cgroup") -> Path:
    """Return this process's cgroup directory, if memory accounting is usable there.

    This does not create or modify any cgroup;
    it is on the caller to arrange for this process
    to already be the sole occupant of its own cgroup,
    e.g. by launching it via `systemd-run --scope`.

    Parameters
    ----------
    cgroup_root
        The cgroup v2 mount point.
        Overridable so tests can point this at a fake tree instead of the real `/sys/fs/cgroup`.

    Returns
    -------
    cgroup_dir
        The absolute path of this process's own cgroup.

    Raises
    ------
    CgroupError
        If cgroup memory accounting is unavailable for any reason:
        not Linux, not cgroup v2, memory accounting not active for this cgroup, etc.
    """
    own_dir = _own_cgroup_dir(cgroup_root)
    # Verify that this process is alone in the cgroup.
    try:
        with open(own_dir / "cgroup.procs") as fh:
            pids = [int(line) for line in fh if line.strip()]
    except (OSError, ValueError) as exc:
        raise CgroupError("Cgroups unavailable: failed to read cgroup.procs.") from exc
    if pids != [os.getpid()]:
        raise CgroupError("Cgroups unavailable: director is not alone in its cgroup.")
    # Verify that memory accounting is actually active for this cgroup.
    try:
        with open(own_dir / "memory.current") as fh:
            fh.read()
    except (OSError, ValueError) as exc:
        raise CgroupError("Cgroups unavailable: failed to read memory.current.") from exc
    logger.info("Cgroup memory accounting enabled: sampling memory.current/peak in %s.", own_dir)
    return own_dir


def _count_cpu_list(text: str) -> int:
    """Count the number of CPU IDs in a cgroup CPU-list string, e.g. `"0-3,7,9-10"`."""
    total = 0
    for token in text.strip().split(","):
        token = token.strip()
        if not token:
            continue
        if "-" in token:
            start_str, end_str = token.split("-", 1)
            total += int(end_str) - int(start_str) + 1
        else:
            total += 1
    return total


def _read_cpuset_ncore(own_dir: Path) -> int | None:
    """Read the number of cores this cgroup is pinned to, or `None` if unavailable."""
    try:
        with open(own_dir / "cpuset.cpus.effective") as fh:
            ncore = _count_cpu_list(fh.read())
    except (OSError, ValueError):
        return None
    return ncore if ncore > 0 else None


def _read_cpu_max_ncore(own_dir: Path) -> float | None:
    """Read the fractional core budget implied by `cpu.max`, or `None` if unavailable."""
    try:
        with open(own_dir / "cpu.max") as fh:
            parts = fh.read().split()
    except OSError:
        return None
    if len(parts) != 2 or parts[0] == "max":
        return None
    try:
        quota, period = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    return quota / period if period > 0 else None


def get_ncore_from_cgroup(cgroup_root: str = "/sys/fs/cgroup") -> int:
    """Determine the number of CPU cores available to this process, from cgroup v2 accounting.

    Combines `cpuset.cpus.effective` (cores pinned to this cgroup)
    and `cpu.max` (a fractional CPU-time quota),
    taking the minimum over whichever of the two is readable,
    since either constraint can independently reduce the effective core budget.

    Unlike `find_own_memory_cgroup()`,
    this does not require the calling process to be the sole occupant of its cgroup:
    a Slurm or PBS job's cgroup is normally shared by all of the job's processes,
    so requiring exclusivity would defeat the purpose of using this
    to detect scheduler-imposed core limits.

    Parameters
    ----------
    cgroup_root
        The cgroup v2 mount point.
        Overridable so tests can point this at a fake tree instead of the real `/sys/fs/cgroup`.

    Returns
    -------
    ncore
        The number of cores available to this process,
        floored to an integer with a minimum of 1.

    Raises
    ------
    CgroupError
        If cgroups are unavailable,
        or if neither `cpuset.cpus.effective` nor `cpu.max` can be read.
    """
    own_dir = _own_cgroup_dir(cgroup_root)
    candidates = [
        ncore
        for ncore in (_read_cpuset_ncore(own_dir), _read_cpu_max_ncore(own_dir))
        if ncore is not None
    ]
    if not candidates:
        raise CgroupError(
            f"Cgroups unavailable: neither cpuset.cpus.effective nor cpu.max "
            f"is readable in {own_dir}."
        )
    return max(1, math.floor(min(candidates)))
