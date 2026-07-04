# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Unit tests for stepup.core.cgroups"""

import os

import pytest

from stepup.core import cgroups
from stepup.core.cgroups import find_own_memory_cgroup, get_ncore_from_cgroup
from stepup.core.exceptions import CgroupError


def test_own_cgroup_path_no_unified_hierarchy(monkeypatch, path_tmp):
    fake_path = path_tmp / "cgroup"
    fake_path.write_text("1:name=systemd:/\n2:cpu,cpuacct:/\n")
    real_open = open

    def fake_open(path, *args, **kwargs):
        if path == "/proc/self/cgroup":
            path = fake_path
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(cgroups, "open", fake_open, raising=False)
    with pytest.raises(CgroupError):
        cgroups._own_cgroup_path()


def test_find_own_memory_cgroup_success(path_tmp):
    (path_tmp / "own").mkdir()
    (path_tmp / "own" / "cgroup.procs").write_text(f"{os.getpid()}\n")
    (path_tmp / "own" / "memory.current").write_text("1048576")
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(cgroups, "_own_cgroup_path", lambda: "own")
        assert find_own_memory_cgroup(cgroup_root=path_tmp) == str(path_tmp / "own")


def test_find_own_memory_cgroup_not_linux(monkeypatch):
    monkeypatch.setattr(cgroups.sys, "platform", "darwin")
    with pytest.raises(CgroupError):
        find_own_memory_cgroup()


def test_find_own_memory_cgroup_no_cgroup_procs(path_tmp, monkeypatch):
    (path_tmp / "own").mkdir()
    monkeypatch.setattr(cgroups, "_own_cgroup_path", lambda: "own")
    with pytest.raises(CgroupError):
        find_own_memory_cgroup(cgroup_root=path_tmp)


def test_find_own_memory_cgroup_not_alone(path_tmp, monkeypatch):
    (path_tmp / "own").mkdir()
    (path_tmp / "own" / "cgroup.procs").write_text(f"{os.getpid()}\n{os.getpid() + 1}\n")
    monkeypatch.setattr(cgroups, "_own_cgroup_path", lambda: "own")
    with pytest.raises(CgroupError):
        find_own_memory_cgroup(cgroup_root=path_tmp)


def test_find_own_memory_cgroup_memory_not_readable(path_tmp, monkeypatch):
    (path_tmp / "own").mkdir()
    (path_tmp / "own" / "cgroup.procs").write_text(f"{os.getpid()}\n")
    monkeypatch.setattr(cgroups, "_own_cgroup_path", lambda: "own")
    with pytest.raises(CgroupError):
        find_own_memory_cgroup(cgroup_root=path_tmp)


@pytest.fixture
def own_dir(path_tmp, monkeypatch):
    """A fake cgroup directory that `get_ncore_from_cgroup` will resolve to."""
    (path_tmp / "own").mkdir()
    monkeypatch.setattr(cgroups, "_own_cgroup_path", lambda: "own")
    return path_tmp / "own"


def test_get_ncore_from_cgroup_cpuset_only(path_tmp, own_dir):
    (own_dir / "cpuset.cpus.effective").write_text("0-3,7,9-10")
    assert get_ncore_from_cgroup(cgroup_root=path_tmp) == 7


def test_get_ncore_from_cgroup_cpu_max_only(path_tmp, own_dir):
    (own_dir / "cpu.max").write_text("350000 100000")
    assert get_ncore_from_cgroup(cgroup_root=path_tmp) == 3


def test_get_ncore_from_cgroup_min_of_both(path_tmp, own_dir):
    (own_dir / "cpuset.cpus.effective").write_text("0-7")
    (own_dir / "cpu.max").write_text("350000 100000")
    assert get_ncore_from_cgroup(cgroup_root=path_tmp) == 3


def test_get_ncore_from_cgroup_unlimited_quota_uses_cpuset(path_tmp, own_dir):
    (own_dir / "cpuset.cpus.effective").write_text("0-3")
    (own_dir / "cpu.max").write_text("max 100000")
    assert get_ncore_from_cgroup(cgroup_root=path_tmp) == 4


def test_get_ncore_from_cgroup_no_files(path_tmp, own_dir):
    with pytest.raises(CgroupError):
        get_ncore_from_cgroup(cgroup_root=path_tmp)


def test_get_ncore_from_cgroup_not_linux(path_tmp, monkeypatch):
    monkeypatch.setattr(cgroups.sys, "platform", "darwin")
    with pytest.raises(CgroupError):
        get_ncore_from_cgroup(cgroup_root=path_tmp)


def test_get_ncore_from_cgroup_fractional_quota_floors_to_minimum_one(path_tmp, own_dir):
    (own_dir / "cpu.max").write_text("50000 100000")
    assert get_ncore_from_cgroup(cgroup_root=path_tmp) == 1


def test_get_ncore_from_cgroup_malformed_cpuset_falls_back_to_cpu_max(path_tmp, own_dir):
    (own_dir / "cpuset.cpus.effective").write_text("not-a-cpu-list")
    (own_dir / "cpu.max").write_text("350000 100000")
    assert get_ncore_from_cgroup(cgroup_root=path_tmp) == 3
