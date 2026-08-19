# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Unit tests for stepup.core.asyncio"""

import asyncio
import contextlib
import os
from functools import partial

import pytest
from core_common import settled_task_names
from path import Path

from stepup.core.asyncio import (
    MAX_POLL_DELAY,
    iter_until_stopped,
    wait_for_any_event,
    wait_for_path,
    wait_for_readable_fd,
)

#
# Waiting for a condition
#


async def test_wait_for_readable_fd():
    read_fd, write_fd = os.pipe()
    try:
        task = asyncio.create_task(wait_for_readable_fd(read_fd), name="readable")
        await asyncio.sleep(0)
        assert not task.done()
        os.write(write_fd, b"x")
        await asyncio.wait_for(task, timeout=5)
        assert os.read(read_fd, 1) == b"x"
    finally:
        os.close(read_fd)
        os.close(write_fd)
    assert await settled_task_names() == []


async def test_wait_for_readable_fd_cancelled():
    """A cancelled wait must unregister its reader, or the next one is refused."""
    read_fd, write_fd = os.pipe()
    try:
        task = asyncio.create_task(wait_for_readable_fd(read_fd), name="readable")
        await asyncio.sleep(0)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        os.write(write_fd, b"x")
        await asyncio.wait_for(wait_for_readable_fd(read_fd), timeout=5)
    finally:
        os.close(read_fd)
        os.close(write_fd)
    assert await settled_task_names() == []


async def test_wait_for_any_event_already_set():
    first = asyncio.Event()
    second = asyncio.Event()
    second.set()
    await asyncio.wait_for(wait_for_any_event(first, second), timeout=5)
    assert await settled_task_names() == []


async def test_wait_for_any_event_set_later():
    first = asyncio.Event()
    second = asyncio.Event()
    task = asyncio.create_task(wait_for_any_event(first, second), name="any_event")
    await asyncio.sleep(0)
    assert not task.done()
    first.set()
    await asyncio.wait_for(task, timeout=5)
    assert await settled_task_names() == []


async def test_wait_for_any_event_none_set():
    first = asyncio.Event()
    second = asyncio.Event()
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(wait_for_any_event(first, second), timeout=0.1)
    assert await settled_task_names() == []


async def test_wait_for_path_exists(path_tmp: Path):
    path = path_tmp / "there"
    path.touch()
    assert await wait_for_path(path, asyncio.Event())


async def test_wait_for_path_appears(path_tmp: Path):
    path = path_tmp / "later"
    stop_event = asyncio.Event()
    task = asyncio.create_task(wait_for_path(path, stop_event), name="wait_for_path")
    await asyncio.sleep(0)
    assert not task.done()
    path.touch()
    assert await asyncio.wait_for(task, timeout=5)
    assert await settled_task_names() == []


async def test_wait_for_path_stopped(path_tmp: Path):
    stop_event = asyncio.Event()
    stop_event.set()
    assert not await wait_for_path(path_tmp / "never", stop_event)


async def test_wait_for_path_delay_is_capped(monkeypatch: pytest.MonkeyPatch, path_tmp: Path):
    """The polling interval must stop growing, or a late path is noticed even later."""
    path = path_tmp / "later"
    delays = []

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)
        if len(delays) == 20:
            path.touch()

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    assert await wait_for_path(path, asyncio.Event())
    assert delays[0] == pytest.approx(0.1)
    assert max(delays) == pytest.approx(MAX_POLL_DELAY)


#
# Stoppable iteration
#


async def test_iter_until_stopped_yields_all_messages():
    queue = asyncio.Queue()
    for item in "abc":
        queue.put_nowait(item)
    stop_event = asyncio.Event()
    items = []
    async for item in iter_until_stopped(queue.get, stop_event):
        items.append(item)
        if len(items) == 3:
            stop_event.set()
    assert items == ["a", "b", "c"]
    assert await settled_task_names() == []


async def test_iter_until_stopped_stops_without_messages():
    queue = asyncio.Queue()
    stop_event = asyncio.Event()
    stop_event.set()
    items = [item async for item in iter_until_stopped(queue.get, stop_event)]
    assert items == []
    assert await settled_task_names() == []


async def test_iter_until_stopped_yields_message_that_already_arrived():
    """A message available before the stop event is set is still yielded.

    The queue is empty after that message, so the next round is decided by the stop event.
    """
    queue = asyncio.Queue()
    queue.put_nowait("a")
    stop_event = asyncio.Event()
    stop_event.set()
    items = [item async for item in iter_until_stopped(queue.get, stop_event)]
    assert items == ["a"]
    assert await settled_task_names() == []


async def test_iter_until_stopped_partial():
    """A `get_next` that takes arguments is bound with `functools.partial`."""

    async def get_next(prefix: str, queue: asyncio.Queue) -> str:
        return prefix + await queue.get()

    queue = asyncio.Queue()
    queue.put_nowait("a")
    stop_event = asyncio.Event()
    si = iter_until_stopped(partial(get_next, "x", queue), stop_event)
    async with contextlib.aclosing(si):
        assert await anext(si) == "xa"
    assert await settled_task_names() == []


async def test_iter_until_stopped_propagates_exception():
    async def get_next() -> str:
        raise ValueError("boom")

    stop_event = asyncio.Event()
    si = iter_until_stopped(get_next, stop_event)
    with pytest.raises(ValueError, match="boom"):
        async for _ in si:
            pass
    assert await settled_task_names() == []


async def test_iter_until_stopped_break_leaves_no_pending_tasks():
    """Regression test: an iterator that a consumer walks away from must not leak tasks.

    Both RPC receive loops in `stepup.core.rpc` break out of the iteration
    when the peer closes the connection.
    Without cleanup, the task waiting for the stop event stays pending forever
    and eventually shows up in the director log as `Task was destroyed but it is pending!`,
    which `DIRECTOR_LOG_CHECKS` reports as a bug in StepUp.
    """
    queue = asyncio.Queue()
    for item in "abc":
        queue.put_nowait(item)
    stop_event = asyncio.Event()
    si = iter_until_stopped(queue.get, stop_event)
    async with contextlib.aclosing(si):
        async for item in si:
            if item == "b":
                break
    assert await settled_task_names() == []


async def test_iter_until_stopped_finalized_leaves_no_pending_tasks():
    """The cleanup also happens without `aclosing`, when the iterator is finalized."""
    queue = asyncio.Queue()
    for item in "abc":
        queue.put_nowait(item)
    stop_event = asyncio.Event()

    async def consume():
        async for item in iter_until_stopped(queue.get, stop_event):
            if item == "b":
                break

    await consume()
    assert await settled_task_names() == []


async def test_iter_until_stopped_cancel_leaves_no_pending_tasks():
    """Regression test: cancelling the consumer must not leak the tasks it was waiting for.

    `asyncio.wait` leaves the tasks it was given pending when the task awaiting it is cancelled.
    """
    queue = asyncio.Queue()
    stop_event = asyncio.Event()

    async def consume():
        async for _ in iter_until_stopped(queue.get, stop_event):
            pass

    task = asyncio.create_task(consume(), name="consume")
    await asyncio.sleep(0)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    assert await settled_task_names() == []
