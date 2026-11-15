# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""The client with which the director reports progress and file changes to `stepup build`."""

import asyncio
import contextlib
import logging
from collections.abc import AsyncGenerator

import attrs

from ..constants import PROGRESS_REFRESH_DELAY
from ..path import Path
from ..rpc.client import BaseAsyncRPCClient, DummyAsyncRPCClient, SocketAsyncRPCClient

__all__ = ("ReporterClient",)

logger = logging.getLogger(__name__)


@attrs.define
class ReporterClient:
    client: BaseAsyncRPCClient = attrs.field(factory=DummyAsyncRPCClient)
    """The RPC client to use for reporting, or a dummy client when there is no reporter."""

    _started_buffer: dict[int, tuple[str, str]] = attrs.field(init=False, factory=dict)
    """Buffered `job_started` signals not yet sent, keyed by `job_i`.

    An entry is removed again when `job_stopped` arrives before the flush.
    """

    _stopped_buffer: set[int] = attrs.field(init=False, factory=set)
    """Buffered `job_stopped` signals not yet sent.

    Only holds `job_i`s whose matching start was already flushed in an earlier batch:
    a start/stop pair arriving within the same delay window is dropped instead
    (see `job_stopped`), since it would never be visible in the progress bar anyway.
    """

    _flush_jobs_handle: asyncio.TimerHandle | None = attrs.field(init=False, default=None)
    """Handle for the scheduled `_flush_jobs` call, or `None` when none is pending."""

    _flush_tasks: set[asyncio.Task] = attrs.field(init=False, factory=set)
    """In-flight `_flush_jobs` tasks.

    Only a task that something still refers to is kept alive by the event loop,
    so a task that is not stored here may be garbage-collected before it has sent its batch.
    """

    _progress_ended: bool = attrs.field(init=False, default=False)
    """Whether progress updates are no longer sent, after `stop_reporting` or `close`.

    Steps and hashes may still end while the director shuts down.
    Their progress signals are then dropped,
    because the progress bar is gone and the client may already be closed.
    """

    @classmethod
    @contextlib.asynccontextmanager
    async def socket(cls, path: Path | None) -> AsyncGenerator["ReporterClient", None]:
        if path is None:
            client = DummyAsyncRPCClient()
        else:
            # The reporter server is hosted in the `stepup build` process, which has no log file.
            client = SocketAsyncRPCClient(
                path, server_log_description="the output of `stepup build`"
            )
        async with cls(client) as reporter:
            yield reporter

    async def __call__(
        self, tag: str, description: str, pages: list[tuple[str, str]] | None = None
    ):
        if pages is None:
            pages = []
        await self.client.call.report(tag, description, pages)

    async def set_njob(self, njob: int):
        await self.client.call.set_njob(njob)

    def job_started(self, job_i: int, letter: str, description: str):
        """Buffer a job-start signal, sent later in a batched `update_running_jobs` RPC call."""
        if self._progress_ended:
            return
        self._started_buffer[job_i] = (letter, description)
        self._request_jobs_flush()

    def job_stopped(self, job_i: int):
        """Buffer a job-stop signal, sent later in a batched `update_running_jobs` RPC call.

        If the matching start is still buffered (started and stopped within the same
        delay window), drop both: the job never needs to appear in the progress bar.
        """
        if self._progress_ended:
            return
        if self._started_buffer.pop(job_i, None) is None:
            self._stopped_buffer.add(job_i)
        self._request_jobs_flush()

    def _request_jobs_flush(self):
        """Schedule `_flush_jobs`, coalescing with any flush already pending."""
        if self._flush_jobs_handle is None:
            loop = asyncio.get_running_loop()
            self._flush_jobs_handle = loop.call_later(
                PROGRESS_REFRESH_DELAY, self._on_flush_jobs_timer
            )

    def _on_flush_jobs_timer(self):
        self._flush_jobs_handle = None
        task = asyncio.get_running_loop().create_task(self._flush_jobs())
        self._flush_tasks.add(task)
        task.add_done_callback(self._flush_tasks.discard)

    async def _flush_jobs(self):
        """Send buffered start/stop job signals in a single batched RPC call."""
        if len(self._started_buffer) == 0 and len(self._stopped_buffer) == 0:
            return
        started, self._started_buffer = self._started_buffer, {}
        stopped, self._stopped_buffer = self._stopped_buffer, set()
        await self.client.call.update_running_jobs(started, stopped)

    async def update_progress(self, ndone: int, ntotal: int):
        if self._progress_ended:
            return
        await self.client.call.update_progress(ndone, ntotal)

    async def warn_about_logs(self):
        await self.client.call.warn_about_logs()

    async def _end_progress(self):
        """Send the buffered job signals and drop all progress signals from now on."""
        self._progress_ended = True
        if self._flush_jobs_handle is not None:
            self._flush_jobs_handle.cancel()
            self._flush_jobs_handle = None
        await self._flush_jobs()
        if len(self._flush_tasks) > 0:
            await asyncio.gather(*self._flush_tasks)

    async def stop_reporting(self):
        """End the progress updates and ask the reporter server to stop."""
        await self._end_progress()
        await self.client.call.stop_reporting()

    async def close(self):
        """End the progress updates and close the client."""
        await self._end_progress()
        await self.client.close()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, tb):
        await self.close()
