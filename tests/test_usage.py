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
"""Unit tests for stepup.core.usage"""

import os
import subprocess
import time
from types import SimpleNamespace

import pytest

from stepup.core.usage import (
    PSS_AVAILABLE,
    ChildOutcome,
    PssSampler,
    ResourceAccumulator,
    ResourceUsage,
    _descendant_pids,
    read_pss_kib,
)


@pytest.mark.skipif(not PSS_AVAILABLE, reason="PSS is only available on Linux")
def test_read_pss_kib_self():
    pss_kib = read_pss_kib(os.getpid())
    assert pss_kib is not None
    assert pss_kib > 0


def test_read_pss_kib_nonexistent_pid():
    # A pid that is very unlikely to exist.
    assert read_pss_kib(2**30 - 1) is None


def test_read_pss_kib_not_available(monkeypatch):
    monkeypatch.setattr("stepup.core.usage.PSS_AVAILABLE", False)
    assert read_pss_kib(os.getpid()) is None


def test_resource_accumulator_add():
    acc = ResourceAccumulator()
    acc.add(1.5, 0.5, 3, 4)
    acc.add(2.5, 1.5, 5, 6)
    assert acc.utime == pytest.approx(4.0)
    assert acc.stime == pytest.approx(2.0)
    assert acc.inblock == 8
    assert acc.oublock == 10


def test_resource_usage_defaults():
    usage = ResourceUsage()
    assert usage.utime == 0.0
    assert usage.stime == 0.0
    assert usage.inblock == 0
    assert usage.oublock == 0


def test_resource_usage_from_rusage_diff_self_only():
    ru_start = SimpleNamespace(ru_utime=1.0, ru_stime=2.0, ru_inblock=3, ru_oublock=4)
    ru_end = SimpleNamespace(ru_utime=1.5, ru_stime=2.25, ru_inblock=5, ru_oublock=9)
    usage = ResourceUsage.from_rusage_diff(ru_start, ru_end)
    assert usage.utime == pytest.approx(0.5)
    assert usage.stime == pytest.approx(0.25)
    assert usage.inblock == 2
    assert usage.oublock == 5


def test_resource_usage_from_rusage_diff_self_and_children():
    ru_self_start = SimpleNamespace(ru_utime=1.0, ru_stime=2.0, ru_inblock=3, ru_oublock=4)
    ru_self_end = SimpleNamespace(ru_utime=1.5, ru_stime=2.25, ru_inblock=5, ru_oublock=9)
    ru_children_start = SimpleNamespace(ru_utime=0.1, ru_stime=0.2, ru_inblock=1, ru_oublock=1)
    ru_children_end = SimpleNamespace(ru_utime=0.4, ru_stime=0.5, ru_inblock=4, ru_oublock=2)
    usage = ResourceUsage.from_rusage_diff(
        ru_self_start, ru_self_end, ru_children_start, ru_children_end
    )
    # self diff (0.5, 0.25, 2, 5) + children diff (0.3, 0.3, 3, 1)
    assert usage.utime == pytest.approx(0.8)
    assert usage.stime == pytest.approx(0.55)
    assert usage.inblock == 5
    assert usage.oublock == 6


def test_child_outcome_fields():
    usage = ResourceUsage(utime=1.0, stime=0.5, inblock=2, oublock=3)
    outcome = ChildOutcome(payload=("ok", 42), usage=usage)
    assert outcome.payload == ("ok", 42)
    assert outcome.usage is usage


def test_resource_accumulator_add_usage():
    acc = ResourceAccumulator()
    acc.add_usage(ResourceUsage(utime=1.5, stime=0.5, inblock=3, oublock=4))
    acc.add_usage(ResourceUsage(utime=2.5, stime=1.5, inblock=5, oublock=6))
    assert acc.utime == pytest.approx(4.0)
    assert acc.stime == pytest.approx(2.0)
    assert acc.inblock == 8
    assert acc.oublock == 10


def test_descendant_pids_excludes_self():
    pids = _descendant_pids(os.getpid())
    assert os.getpid() not in pids


def test_descendant_pids_nonexistent_pid():
    # A pid that is very unlikely to exist: no children to walk, so an empty result.
    assert _descendant_pids(2**30 - 1) == set()


@pytest.mark.skipif(not PSS_AVAILABLE, reason="/proc children walk is Linux-only")
def test_descendant_pids_includes_grandchild_process():
    # "sleep 5 & wait" forks a real grandchild instead of sh exec-replacing itself,
    # so this confirms the walk recurses past direct children.
    proc = subprocess.Popen(["sh", "-c", "sleep 5 & wait"])
    try:
        # The background "sleep" is forked asynchronously by the shell, so poll
        # briefly instead of assuming it already exists right after Popen() returns.
        deadline = time.monotonic() + 2.0
        pids = _descendant_pids(os.getpid())
        while len(pids) < 2 and time.monotonic() < deadline:
            pids = _descendant_pids(os.getpid())
        assert proc.pid in pids
        assert len(pids) >= 2
    finally:
        proc.terminate()
        proc.wait()


@pytest.mark.skipif(not PSS_AVAILABLE, reason="PSS is only available on Linux")
def test_pss_sampler_sample_once_tracks_peak():
    # sample_once() only sums PSS over descendants, so a live child is needed
    # for a sample to have anything readable.
    proc = subprocess.Popen(["sleep", "2"])
    try:
        sampler = PssSampler()
        assert sampler.nsamples == 0
        assert sampler.peak_kib == 0
        sampler.sample_once()
        assert sampler.nsamples == 1
        assert sampler.peak_kib > 0
        peak_after_first = sampler.peak_kib
        sampler.sample_once()
        assert sampler.nsamples == 2
        assert sampler.peak_kib >= peak_after_first
    finally:
        proc.terminate()
        proc.wait()


def test_pss_sampler_sample_once_noop_when_unavailable(monkeypatch):
    monkeypatch.setattr("stepup.core.usage.PSS_AVAILABLE", False)
    sampler = PssSampler()
    sampler.sample_once()
    assert sampler.nsamples == 0
    assert sampler.peak_kib == 0
