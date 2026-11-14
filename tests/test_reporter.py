# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Unit tests for the `ReporterClient` in stepup.core.reporter."""

import asyncio

import attrs
import pytest

from stepup.core.exceptions import RPCClientUnusableError
from stepup.core.reporter import PROGRESS_REFRESH_DELAY, ReporterClient
from stepup.core.rpc import BaseAsyncRPCClient


@attrs.define
class RecordingAsyncClient(BaseAsyncRPCClient):
    """An async client recording its calls and, like a socket client, refuses them once closed."""

    calls: list[tuple[str, tuple]] = attrs.field(init=False, factory=list)
    """The names and positional arguments of the calls received so far."""

    closed: bool = attrs.field(init=False, default=False)
    """Whether `close()` was called."""

    async def __call__(self, name: str, /, *args, **kwargs):
        if self.closed:
            raise RPCClientUnusableError("The recording client is closed.")
        self.calls.append((name, args))

    async def close(self):
        self.closed = True


async def test_close_flushes_buffered_jobs():
    client = RecordingAsyncClient()
    reporter = ReporterClient(client)
    reporter.job_started(1, "S", "first")
    await reporter.close()
    assert client.calls == [("update_running_jobs", ({1: ("S", "first")}, set()))]


async def test_stop_reporting_flushes_before_stopping():
    client = RecordingAsyncClient()
    reporter = ReporterClient(client)
    reporter.job_started(1, "S", "first")
    await reporter.stop_reporting()
    assert [name for name, _ in client.calls] == ["update_running_jobs", "stop_reporting"]


async def test_progress_after_stop_reporting_is_dropped():
    client = RecordingAsyncClient()
    reporter = ReporterClient(client)
    await reporter.stop_reporting()
    reporter.job_started(1, "S", "late")
    reporter.job_stopped(1)
    reporter.job_stopped(2)
    await reporter.update_progress(1, 2)
    await asyncio.sleep(PROGRESS_REFRESH_DELAY * 2)
    await reporter.close()
    assert client.calls == [("stop_reporting", ())]


async def test_socket_closes_reporter_so_late_jobs_do_not_fail(monkeypatch):
    """A job that stops during the shutdown must not reach the closed client."""
    client = RecordingAsyncClient()
    monkeypatch.setattr("stepup.core.reporter.DummyAsyncRPCClient", lambda: client)
    async with ReporterClient.socket(None) as reporter:
        reporter.job_started(1, "S", "step")
        await asyncio.sleep(PROGRESS_REFRESH_DELAY * 2)
    reporter.job_stopped(1)
    errors = []
    loop = asyncio.get_running_loop()
    loop.set_exception_handler(lambda _loop, context: errors.append(context))
    await asyncio.sleep(PROGRESS_REFRESH_DELAY * 2)
    assert errors == []
    assert client.calls == [("update_running_jobs", ({1: ("S", "step")}, set()))]
    with pytest.raises(RPCClientUnusableError):
        await client.call.update_progress(0, 1)
