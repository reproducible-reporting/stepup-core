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
from asyncinotify import Inotify, Mask, Watch
from path import Path

from .asyncio import stoppable_iterator
from .file import FileState
from .reporter import ReporterClient
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
        async with AsyncInotifyWrapper(self.dir_queue) as wrapper:
            while not stop_event.is_set():
                await self.resume.wait()
                await self.watch_changes(wrapper.change_queue)
                self.resume.clear()

    async def watch_changes(self, change_queue: asyncio.Queue):
        """Watch file events. They are sent to the workflow right before the runner is restarted."""
        self.files_changed.clear()

        # Process changes to static files picked up during watch phase.
        while not change_queue.empty():
            change, path = change_queue.get_nowait()
            if self.workflow.file_states.get(f"file:{path}") == FileState.STATIC:
                await self.record_change(change, path)

        # Wait for new changes to show up.
        self.active.set()
        await self.reporter("PHASE", "watch")
        async for change, path in stoppable_iterator(change_queue.get, self.interrupt):
            await self.record_change(change, path)

        # Feed all updates to the worker and clean up.
        self.active.clear()
        self.workflow.process_watcher_changes(self.deleted, self.updated)
        self.deleted.clear()
        self.updated.clear()
        self.files_changed.clear()
        self.interrupt.clear()

    async def record_change(self, change: Change, path: Path):
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
                # When a directory is added, create a watcher early,
                # to catch events in this directory.
                # All files already present are also considered to be updated.
                if path.endswith("/"):
                    self.dir_queue.put_nowait((False, path))
                    for sub_path in path.iterdir():
                        await self.record_change(Change.UPDATED, sub_path)


@attrs.define
class AsyncInotifyWrapper:
    """Interface between a `Watcher` instance and the `asyncinotify` library."""

    dir_queue: asyncio.Queue = attrs.field()
    """The dir_queue provides directories to (un)watch.

    Each item is a tuple `(remove, path)`, where `remove` is True when the path
    not longer needs to be watched.
    """

    inotify: Inotify | None = attrs.field(init=False, default=None)
    """Inotify object, only present in context."""

    stop_event: asyncio.Event = attrs.field(init=False, factory=asyncio.Event)
    """Internal stop event, called when context is closed."""

    watches: dict[str, Watch] = attrs.field(init=False, factory=dict)
    """Directory of watches created with asyncinotify"""

    change_queue: asyncio.Queue = attrs.field(init=False, factory=asyncio.Queue)
    """A queue object holding file changes received from asyncinotify.

    Each item is a tuple with a `Change` instance and a path."""

    dir_loop_task: asyncio.Task | None = attrs.field(init=False, default=None)
    """Task corresponding to the dir_loop method."""

    change_loop_task: asyncio.Task | None = attrs.field(init=False, default=None)
    """Task corresponding to the change_loop method."""

    async def __aenter__(self):
        """Start using the Inotify Wrapper."""
        self.inotify = Inotify()
        self.stop_event.clear()
        self.dir_loop_task = asyncio.create_task(self.dir_loop())
        self.change_loop_task = asyncio.create_task(self.change_loop())
        return self

    async def __aexit__(self, exc_type, exc, tb):
        """Close the InotifyWrapper."""
        self.stop_event.set()
        await asyncio.gather(self.dir_loop_task, self.change_loop_task)
        self.dir_loop_task = None
        self.change_loop_task = None
        self.inotify.close()
        self.inotify = None

    async def dir_loop(self):
        """Add or remove directories to watch, as soon as they are created or defined static.

        For every directory added, an event handler for watch_dog is created, which puts
        the observed file events to the change_queue.

        Parameters
        ----------
        stop_event
            Event to interrupt processing items from the dir_queue.
        """
        async for remove, path in stoppable_iterator(self.dir_queue.get, self.stop_event):
            if remove:
                watch = self.watches.pop(path, None)
                if watch is not None:
                    self.inotify.rm_watch(watch)
            else:
                if not path.is_dir():
                    raise FileNotFoundError(f"Cannot watch non-existing directory: {path}")
                if path not in self.watches:
                    self.watches[path] = self.inotify.add_watch(
                        path,
                        (
                            Mask.MODIFY
                            | Mask.CREATE
                            | Mask.DELETE
                            | Mask.CLOSE_WRITE
                            | Mask.MOVE
                            | Mask.MOVE_SELF
                            | Mask.DELETE_SELF
                            | Mask.UNMOUNT
                            | Mask.ATTRIB
                            | Mask.IGNORED
                        ),
                    )

    async def change_loop(self):
        """Collect from INotify and translate then to items for the change_queue."""
        async for event in stoppable_iterator(self.inotify.get, self.stop_event):
            # Drop invalid watches (deleted or unmounted directories)
            if event.mask & Mask.IGNORED:
                self.watches.pop(f"{event.watch.path}/", None)
            if event.mask & Mask.MOVE_SELF:
                raise RuntimeError("StepUp does not support moving directories it is watching.")
            change = (
                Change.DELETED
                if event.mask & (Mask.DELETE | Mask.DELETE_SELF | Mask.MOVED_FROM)
                else Change.UPDATED
            )
            path = Path(event.path)
            if event.mask & Mask.ISDIR:
                path = path / ""
            self.change_queue.put_nowait((change, path))
