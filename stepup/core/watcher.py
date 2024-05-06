# StepUp Core provides the basic framework for the StepUp build tool.
# Copyright (C) 2024 Toon Verstraelen
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
"""Watch for file changes and update the workflow accordingly."""

import asyncio
import enum

import attrs
from path import Path
from watchdog.events import FileSystemEvent, FileSystemEventHandler, FileSystemMovedEvent
from watchdog.observers import Observer

from .asyncio import stoppable_iterator
from .file import FileState
from .reporter import ReporterClient
from .utils import myabsolute, myrelpath
from .workflow import Workflow

__all__ = ("Watcher",)


class Change(enum.Enum):
    UPDATED = 41
    DELETED = 42


@attrs.define
class Watcher:
    # The workflow to report file events to.
    workflow: Workflow = attrs.field()

    # The reporter to send progress information to.
    reporter: ReporterClient = attrs.field()

    # The dir_queue is used to set the list of directories to watch for file events.
    # It holds tuples of a boolean and a path. When the boolean is `True`, the path
    # is a directory to remove from the watcher. When `False`, it should be added.
    dir_queue: asyncio.Queue = attrs.field()

    # The active event is set when the Watcher is ready to watch file system events.
    active: asyncio.Event = attrs.field(factory=asyncio.Event)

    # The interrupt event is set when other parts of StepUp want to interrupt the watcher.
    interrupt: asyncio.Event = attrs.field(factory=asyncio.Event)

    # The resume event is set when other parts of StepUp (the runner) want to enable the watcher.
    resume: asyncio.Event = attrs.field(factory=asyncio.Event)

    # A queue object holding file changes received from the watchdog observer,
    # which is running in a separate thread. Each item is in stance of `Change` and path.
    change_queue: asyncio.Queue = attrs.field(init=False, factory=asyncio.Queue)

    # The following sets contain the deleted and changed file
    # while the watcher is observing. These changes are not sent to the workflow yet.
    deleted: set[Path] = attrs.field(init=False, factory=set)
    updated: set[Path] = attrs.field(init=False, factory=set)

    # Event set to True when a relevant file event was recorded.
    # This is used by the watch_update and watch_delete functions.
    files_changed: asyncio.Event = attrs.field(init=False, factory=asyncio.Event)

    async def loop(self, stop_event: asyncio.Event):
        """The main watcher loop.

        Parameters
        ----------
        stop_event
            The main watcher loop is interrupted by this event.

        Notes
        -----
        One iteration in the main watcher loop consists of observing multi file events.
        The iteration ends by informing the workflow of all the changes, after which
        StepUp starts the runner again (or exists).
        """
        dir_loop = asyncio.create_task(self.dir_loop(stop_event))
        while not stop_event.is_set():
            await self.resume.wait()
            # Check for problems with non-existing directories and raise early if needed
            if dir_loop.done() and dir_loop.exception() is not None:
                await dir_loop
            await self.change_loop()
            self.resume.clear()
        await dir_loop

    async def dir_loop(self, stop_event: asyncio.Event):
        """Add or remove directories to watch, as soon as they are created or defined static.

        For every directory added, an event handler for watch_dog is created, which puts
        the observed file events to the change_queue.

        Parameters
        ----------
        stop_event
            Event to interrupt processing items from the dir_queue.
        """
        observer = Observer()
        observer.start()
        watches = {}
        try:
            async for remove, path in stoppable_iterator(self.dir_queue.get, stop_event):
                if remove:
                    watch = watches.pop(path, None)
                    if watch is not None:
                        observer.unschedule(watch)
                else:
                    if not path.is_dir():
                        raise FileNotFoundError(f"Cannot watch non-existing directory: {path}")
                    if path not in watches:
                        handler = QueueEventHandler(self.change_queue, Path(path).isabs())
                        watches[path] = observer.schedule(handler, path)
        finally:
            observer.stop()

    async def change_loop(self):
        """Watch file events. They are sent to the workflow right before the runner is restarted."""
        self.files_changed.clear()

        # Process changes to static files picked up during watch phase.
        while not self.change_queue.empty():
            change, path = self.change_queue.get_nowait()
            if self.workflow.file_states.get(f"file:{path}") == FileState.STATIC:
                await self.record_change(change, path)

        # Wait for new changes to show up.
        self.active.set()
        await self.reporter("PHASE", "watch")
        async for change, path in stoppable_iterator(self.change_queue.get, self.interrupt):
            await self.record_change(change, path)

        # Feed all updates to the worker and clean up.
        self.active.clear()
        self.workflow.process_watcher_changes(self.deleted, self.updated)
        self.deleted.clear()
        self.updated.clear()
        self.files_changed.clear()
        self.interrupt.clear()

    async def record_change(self, change, path):
        """Record a single event taken from the change_queue."""
        if change == Change.DELETED and path not in self.deleted:
            if self.workflow.is_relevant(path):
                await self.reporter("DELETED", path)
                self.deleted.add(path)
                self.updated.discard(path)
                self.files_changed.set()
        elif change == Change.UPDATED and path not in self.updated:  # noqa: SIM102
            if self.workflow.is_relevant(path):
                await self.reporter("UPDATED", path)
                self.deleted.discard(path)
                self.updated.add(path)
                self.files_changed.set()


class QueueEventHandler(FileSystemEventHandler):
    """A file system event handler for watchdog that puts events safely on an asyncio queue.

    Parameters
    ----------
    queue
        The queue on which file events are put.
    is_absolute
        True when the directory being watch is known to StepUp as an absolute path.
    """

    def __init__(self, queue: asyncio.Queue, is_absolute: bool):
        self._queue = queue
        self._is_absolute = is_absolute
        self._loop = asyncio.get_event_loop()

    def on_any_event(self, event: FileSystemEvent):
        """Process any event received from the watchdog observer."""
        if isinstance(event, FileSystemMovedEvent):
            self.put_event(Change.DELETED, event.src_path)
            self.put_event(Change.UPDATED, event.dest_path)
        elif event.event_type in ["created", "modified", "closed"]:
            self.put_event(Change.UPDATED, event.src_path)
        elif event.event_type == "deleted":
            self.put_event(Change.DELETED, event.src_path)
        elif event.event_type != "opened":
            raise NotImplementedError(f"Cannot handle event: {event}")

    def put_event(self, change: Change, path: str):
        """Put an event on the queue, includes translation of abs/rel path."""
        path = myabsolute(path) if self._is_absolute else myrelpath(path)
        # The event calls from watch dog live in a separate thread ...
        self._loop.call_soon_threadsafe(self._queue.put_nowait, (change, path))
