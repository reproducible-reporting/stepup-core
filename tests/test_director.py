# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Unit tests for stepup.core.director."""

import asyncio
import signal
import time
from decimal import Decimal

import attrs
import pytest

from stepup.core import director
from stepup.core.director import SCHEDULER_CPU_ENV_VARS, DirectorHandler, interpret_jobs
from stepup.core.exceptions import CgroupError
from stepup.core.reporter import ReporterClient


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


#
# Terminal signal handling and shutdown escalation
#


@attrs.define
class FakeScheduler:
    on_hold: bool = attrs.field(default=False)


@attrs.define
class FakeExecutor:
    """Records the signals sent to running steps."""

    signals: list[int] = attrs.field(init=False, factory=list)

    def interrupt(self, sig: int) -> None:
        self.signals.append(sig)


@attrs.define
class FakeBuilder:
    """Stands in for the builder, with control over whether steps are still running."""

    running_tasks: list = attrs.field(factory=list)


async def drain_interrupt(handler: DirectorHandler) -> None:
    """Wait for the in-flight interrupt task, which `serve()` cancels in its teardown instead."""
    await handler._interrupt_task


def make_director_handler(nrunning: int = 0) -> DirectorHandler:
    """Create a `DirectorHandler` with just enough collaborators for shutdown tests."""
    return DirectorHandler(
        scheduler=FakeScheduler(),
        workflow=None,
        db=None,
        reporter=ReporterClient(),
        executor=FakeExecutor(),
        builder=FakeBuilder(list(range(nrunning))),
        watcher=None,
        stop_event=asyncio.Event(),
    )


async def test_shutdown_first_call_is_graceful():
    """The first `q` press only stops scheduling: running steps are left alone."""
    handler = make_director_handler(nrunning=1)
    await handler.shutdown()
    assert handler.stop_event.is_set()
    assert handler.scheduler.on_hold
    assert handler.executor.signals == []


async def test_shutdown_holds_the_scheduler_before_reporting():
    """`q` must stop dispatch before it awaits the reporter, not after."""
    handler = make_director_handler(nrunning=1)
    on_hold_when_reported = []

    async def fake_report(action, description, pages=None):
        on_hold_when_reported.append(handler.scheduler.on_hold)

    handler.reporter = fake_report
    await handler.shutdown()
    assert on_hold_when_reported == [True]


async def test_shutdown_escalates_to_sigint_then_sigkill():
    """The documented `q` ladder: graceful, SIGINT, SIGKILL."""
    handler = make_director_handler(nrunning=1)
    await handler.shutdown()
    await handler.shutdown()
    assert handler.executor.signals == [signal.SIGINT]
    await handler.shutdown()
    assert handler.executor.signals == [signal.SIGINT, signal.SIGKILL]
    # Any further press stays at the nuclear option.
    await handler.shutdown()
    assert handler.executor.signals == [signal.SIGINT, signal.SIGKILL, signal.SIGKILL]


async def test_interrupt_aborts_without_waiting():
    """A terminal signal aborts the build instead of waiting for running steps."""
    handler = make_director_handler(nrunning=0)
    handler.interrupt(signal.SIGINT)
    await drain_interrupt(handler)
    assert handler.stop_event.is_set()
    assert handler.scheduler.on_hold
    assert handler.executor.signals == [signal.SIGINT]


async def test_interrupt_then_shutdown_escalates_straight_to_sigkill():
    """A `q` press after a terminal signal escalates, instead of re-sending `SIGINT`."""
    handler = make_director_handler(nrunning=0)
    handler.interrupt(signal.SIGINT)
    await drain_interrupt(handler)
    assert handler.executor.signals == [signal.SIGINT]
    await handler.shutdown()
    assert handler.executor.signals == [signal.SIGINT, signal.SIGKILL]


async def test_interrupt_kills_steps_after_grace(monkeypatch):
    """A step that ignores the interrupt is killed, so StepUp can always exit."""
    monkeypatch.setattr(director, "INTERRUPT_GRACE", 0.2)
    handler = make_director_handler(nrunning=1)
    handler.interrupt(signal.SIGTERM)
    await drain_interrupt(handler)
    assert handler.executor.signals == [signal.SIGINT, signal.SIGKILL]


async def test_interrupt_second_signal_skips_the_grace(monkeypatch):
    """A second Ctrl-C cuts the grace period short instead of adding a rung."""
    monkeypatch.setattr(director, "INTERRUPT_GRACE", 30.0)
    handler = make_director_handler(nrunning=1)
    time_start = time.monotonic()
    handler.interrupt(signal.SIGINT)
    handler.interrupt(signal.SIGINT)
    await drain_interrupt(handler)
    assert handler.executor.signals == [signal.SIGINT, signal.SIGKILL]
    assert time.monotonic() - time_start < 5.0


async def test_cancel_interrupt_cancels_a_pending_grace(monkeypatch):
    """When the build ends in time, shutdown must not wait out the grace period."""
    monkeypatch.setattr(director, "INTERRUPT_GRACE", 30.0)
    handler = make_director_handler(nrunning=1)
    time_start = time.monotonic()
    handler.interrupt(signal.SIGINT)
    # Let the interrupt task reach its grace period, as it would while the build winds down.
    await asyncio.sleep(0.05)
    await handler.cancel_interrupt()
    assert time.monotonic() - time_start < 5.0
    assert handler.executor.signals == [signal.SIGINT]
