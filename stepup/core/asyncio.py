# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Asyncio utilities used in StepUp."""

import asyncio
import contextlib
import functools
import signal
from collections.abc import AsyncIterator, Callable, Coroutine, Iterator, Mapping
from types import FrameType
from typing import Any, TypeVar

from .path import Path

__all__ = (
    "iter_until_stopped",
    "signal_handlers",
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


#
# Signals
#


@contextlib.contextmanager
def signal_handlers(callbacks: Mapping[signal.Signals, Callable[[], object]]) -> Iterator[None]:
    """Handle signals in the running event loop, for the duration of a `with` block.

    A callback runs in the event loop, like any other callback,
    and not in between two arbitrary instructions, as a handler of `signal.signal` does.
    It can therefore use everything that `asyncio` offers.

    This works with every event loop,
    also with the Proactor event loop of Windows, which has no `add_signal_handler`.
    The block must be entered and left in the main thread.

    Parameters
    ----------
    callbacks
        The function to call without arguments for each signal to handle.
        Use `functools.partial` when it takes arguments.
    """
    loop = asyncio.get_running_loop()
    added = []
    replaced = {}
    try:
        for sig, callback in callbacks.items():
            try:
                loop.add_signal_handler(sig, callback)
                added.append(sig)
            except NotImplementedError:
                # The interpreter runs the handler in the main thread, after the event loop
                # has been woken up through its wake-up socket (`signal.set_wakeup_fd`).
                replaced[sig] = signal.signal(sig, functools.partial(_relay_signal, loop, callback))
        yield
    finally:
        for sig in added:
            loop.remove_signal_handler(sig)
        for sig, previous in replaced.items():
            # `None` stands for a handler that was not installed from Python.
            signal.signal(sig, signal.SIG_DFL if previous is None else previous)


def _relay_signal(
    loop: asyncio.AbstractEventLoop,
    callback: Callable[[], object],
    signum: int,
    frame: FrameType | None,
) -> None:
    """Pass a signal on to the event loop, as a handler for `signal.signal`."""
    loop.call_soon_threadsafe(callback)


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
