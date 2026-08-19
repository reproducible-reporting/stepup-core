# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Asyncio utilities used in StepUp."""

import asyncio
import sys
from collections.abc import AsyncIterator, Callable, Coroutine
from typing import Any, TypeVar

from path import Path

__all__ = (
    "_wait_closed_compat",
    "iter_until_stopped",
    "wait_for_any_event",
    "wait_for_path",
    "wait_for_readable_fd",
)

MessageType = TypeVar("MessageType")


#
# Waiting for a condition
#


async def wait_for_readable_fd(fd: int) -> None:
    """Wait until file descriptor `fd` becomes readable."""
    loop = asyncio.get_running_loop()
    future = loop.create_future()

    def _on_readable():
        if not future.done():
            future.set_result(None)

    loop.add_reader(fd, _on_readable)
    try:
        await future
    finally:
        loop.remove_reader(fd)


async def wait_for_any_event(*events: asyncio.Event) -> None:
    """Wait until at least one of the events is set."""
    tasks = [asyncio.create_task(event.wait(), name="wait_for_event") for event in events]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()


MAX_POLL_DELAY = 0.5
"""The upper limit of the polling interval of `wait_for_path`, in seconds."""


async def wait_for_path(path: Path, stop_event: asyncio.Event) -> bool:
    """Wait until the path exists or `stop_event` is set.

    Parameters
    ----------
    path
        The path to wait for.
    stop_event
        Give up waiting when this event is set.

    Returns
    -------
    exists
        `True` when the path exists,
        `False` when `stop_event` was set before the path appeared.
    """
    delay = 0.0
    while not path.exists():
        if stop_event.is_set():
            return False
        if delay > 0:
            await asyncio.sleep(delay)
        # The delay is capped because it also bounds how long it takes to notice `stop_event`.
        delay = min(delay + 0.1, MAX_POLL_DELAY)
    return True


async def _wait_closed_compat(server: asyncio.Server):
    """Wait for the connections of a closed server, on the versions where `wait_closed` does not.

    `asyncio.Server.wait_closed` returned without waiting for the connections still being served,
    which was fixed in Python 3.12.1, see https://github.com/python/cpython/issues/120866.
    This helper can go when StepUp requires Python 3.12.1 or later.
    """
    if sys.version_info >= (3, 12, 1) or server._waiters is None:
        return
    waiter = server.get_loop().create_future()
    server._waiters.append(waiter)
    await waiter


#
# Stoppable iteration
#


async def iter_until_stopped(
    get_next: Callable[[], Coroutine[Any, Any, MessageType]], stop_event: asyncio.Event
) -> AsyncIterator[MessageType]:
    """Iterate over the messages returned by `get_next`, until `stop_event` is set.

    Parameters
    ----------
    get_next
        A coroutine function producing the next message.
        Use `functools.partial` when it takes arguments.
    stop_event
        When this event is set, the loop is interrupted.

    Notes
    -----
    The stop event competes with the next message,
    so messages that are already available when `stop_event` is set are still yielded.
    The iteration ends at the first message that does not arrive
    before the stop event is noticed.
    This race is not a way to drain the remaining messages:
    a consumer that may not lose messages must keep reading until the source is exhausted.

    A consumer that may break out of the iteration should wrap it in `contextlib.aclosing`.
    The tasks waiting for the stop event and the next message are then cleaned up right away,
    instead of when the garbage collector finalizes the iterator,
    which may never happen when the event loop is already shutting down.
    """
    stop_task = asyncio.create_task(stop_event.wait(), name="stop_task")
    next_task = None
    try:
        while True:
            next_task = asyncio.create_task(get_next(), name="next_task")
            done, _ = await asyncio.wait(
                [next_task, stop_task], return_when=asyncio.FIRST_COMPLETED
            )
            if next_task not in done:
                break
            yield await next_task
    finally:
        # Also needed when the consumer stops iterating early or is cancelled:
        # `asyncio.wait` leaves both tasks pending, and an abandoned pending task
        # ends up in the director log as a symptom of a bug.
        stop_task.cancel()
        if next_task is not None:
            next_task.cancel()
