# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Unit tests for stepup.core.director."""

from decimal import Decimal

import pytest

from stepup.core import director
from stepup.core.director import SCHEDULER_CPU_ENV_VARS, interpret_jobs
from stepup.core.exceptions import CgroupError


@pytest.fixture(autouse=True)
def _no_scheduler_env(monkeypatch):
    """Prevent scheduler environment variables from leaking into the tests."""
    for var in SCHEDULER_CPU_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture(autouse=True)
def _no_cgroup(monkeypatch):
    """Disable cgroup v2 detection so it doesn't shadow the env var/affinity tests below.

    Individual tests that want to exercise the cgroup path override this again.
    """

    def _raise():
        raise CgroupError("disabled for tests")

    monkeypatch.setattr(director, "get_ncore_from_cgroup", _raise)


def test_interpret_jobs_integer():
    assert interpret_jobs(Decimal("4")) == 4


def test_interpret_jobs_fraction_affinity(monkeypatch):
    monkeypatch.setattr("os.sched_getaffinity", lambda pid: set(range(8)), raising=False)
    result = interpret_jobs(Decimal("1.5"))
    assert result == 12


def test_interpret_jobs_fraction_cpu_count(monkeypatch):
    monkeypatch.delattr("os.sched_getaffinity", raising=False)
    monkeypatch.setattr("os.cpu_count", lambda: 8)
    result = interpret_jobs(Decimal("1.5"))
    assert result == 12


@pytest.mark.parametrize("var", SCHEDULER_CPU_ENV_VARS)
def test_interpret_jobs_scheduler_env(monkeypatch, var):
    monkeypatch.setattr("os.sched_getaffinity", lambda pid: set(range(8)), raising=False)
    monkeypatch.setenv(var, "6")
    result = interpret_jobs(Decimal("1.5"))
    assert result == 9


def test_interpret_jobs_scheduler_env_priority(monkeypatch):
    monkeypatch.setenv("SLURM_CPUS_PER_TASK", "6")
    monkeypatch.setenv("PBS_NUM_PPN", "100")
    monkeypatch.setenv("NCPUS", "100")
    result = interpret_jobs(Decimal("1.5"))
    assert result == 9


def test_interpret_jobs_cgroup_priority(monkeypatch):
    monkeypatch.setattr(director, "get_ncore_from_cgroup", lambda: 4)
    monkeypatch.setenv("SLURM_CPUS_PER_TASK", "100")
    monkeypatch.setattr("os.sched_getaffinity", lambda pid: set(range(100)), raising=False)
    result = interpret_jobs(Decimal("1.5"))
    assert result == 6


def test_interpret_jobs_cgroup_error_falls_back_to_env(monkeypatch):
    monkeypatch.setenv("SLURM_CPUS_PER_TASK", "6")
    result = interpret_jobs(Decimal("1.5"))
    assert result == 9
