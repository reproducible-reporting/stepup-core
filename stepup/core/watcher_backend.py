# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Common interface of the backends that watch the file system for `Watcher`."""

import abc
import asyncio
from typing import Any

import attrs

from .enums import Change
from .path import Path

__all__ = ("WatcherBackend",)


@attrs.define
class WatcherBackend(abc.ABC):
    """Interface between a `Watcher` instance and a library that watches the file system.

    A backend turns the directories received on `dir_queue` into watches
    and the resulting file events into `(Change, path)` items on `change_queue`,
    which is the only thing `Watcher` consumes.
    Only the changes of files are reported, apart from the removal of a watched directory,
    which is reported as `Change.DELETED_PARENT`.
    A backend is used as an asynchronous context manager,
    which starts its background tasks on entering and stops them on leaving.
    """

    dir_queue: asyncio.Queue[Path] = attrs.field(kw_only=True)
    """The dir_queue provides directories to watch.

    Only new watches can be installed. Existing watches cannot be removed,
    but will be removed automatically when the directory is deleted.
    """

    stop_event: asyncio.Event = attrs.field(init=False, factory=asyncio.Event)
    """Internal stop event, set when the context is closed or a background task fails."""

    change_queue: asyncio.Queue[tuple[Change, Path]] = attrs.field(
        init=False, factory=asyncio.Queue
    )
    """A queue object holding the file changes reported by the backend."""

    watches: dict[Path, Any] = attrs.field(init=False, factory=dict)
    """The directories of interest, keyed by their normalized path.

    A directory that must be watched but has no watch installed yet is present with `None`,
    either because it does not exist or because the watch has been dropped.
    A directory absent from this dict is one StepUp has no interest in.
    A backend defines what a value other than `None` is.
    """

    dir_loop_task: asyncio.Task | None = attrs.field(init=False, default=None)
    """Task corresponding to the dir_loop method."""

    change_loop_task: asyncio.Task | None = attrs.field(init=False, default=None)
    """Task corresponding to the change_loop method."""

    @abc.abstractmethod
    async def __aenter__(self):
        """Start the backend and return it."""

    @abc.abstractmethod
    async def __aexit__(self, exc_type, exc, tb):
        """Stop the backend, and raise the exception of a background task that has failed."""

    @abc.abstractmethod
    async def flush(self):
        """Wait until the changes made before this call are on the change_queue."""

    @abc.abstractmethod
    async def dir_loop(self):
        """Install a watch for every directory received from the dir_queue.

        A directory that does not exist yet is recorded without a watch,
        just like one that was watched before and has been removed since.
        Its nearest existing ancestor is watched instead,
        so `change_loop` sees it appear and installs the pending watch at that moment.
        """

    @abc.abstractmethod
    async def change_loop(self):
        """Translate the events of the library into items for the change_queue.

        When a directory of interest appears, its pending watch is installed
        and its contents are reported, so nothing created in the gap is missed.
        """

    def _signal_stop_on_error(self, task: asyncio.Future):
        """Wake the parent loop when a background task fails.

        The exception itself is not logged here,
        because `__aexit__` re-raises it, with its traceback, when it gathers the tasks.
        A cancelled task is left alone, since cancellation is how the backend shuts down.
        """
        if not task.cancelled() and task.exception() is not None:
            self.stop_event.set()
