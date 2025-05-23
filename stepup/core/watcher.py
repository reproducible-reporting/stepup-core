# StepUp Core provides the basic framework for the StepUp build tool.
# © 2024–2025 Toon Verstraelen
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
import sys

import attrs
from path import Path

from .asyncio import stoppable_iterator
from .enums import Change, DirWatch
from .file import File, FileState
from .reporter import ReporterClient
from .utils import DBLock
from .workflow import Workflow

WATCHER_AVAILABLE = sys.platform == "linux"
if WATCHER_AVAILABLE:
    from asyncinotify import Inotify, Mask, Watch
else:
    # Dummy classes for non-linux platforms to avoid type errors below.
    class Inotify:
        pass

    class Mask:
        pass

    class Watch:
        pass


__all__ = ("WATCHER_AVAILABLE", "Watcher")


@attrs.define
class Watcher:
    workflow: Workflow = attrs.field()
    """The workflow to report file events to."""

    dblock: DBLock = attrs.field()
    """Lock for workflow database access.

    This is only used for workflow calls that may change the database.
    """

    reporter: ReporterClient = attrs.field()
    """The reporter to send progress information to."""

    dir_queue: asyncio.Queue = attrs.field()
    """Queue to receive directories to watch (or stop watching) for file events.

    It holds tuples of a `DirWatch` flag and a path.
    If the flag is `DirWatch.START`, the path is a directory to add to the watcher.
    In case of `DirWatch.STOP`, it will be removed from the watched directories.
    """

    active: asyncio.Event = attrs.field(factory=asyncio.Event)
    """The active event is set when the Watcher is reporting file system events.

    It always watches for changes, but only reports them when active.
    """

    processed: asyncio.Event = attrs.field(factory=asyncio.Event)
    """The processed event is set when the Watcher has passed all information to the workflow.

    After this event the run phase can start.
    """

    interrupt: asyncio.Event = attrs.field(factory=asyncio.Event)
    """Event set when other parts of StepUp want to interrupt the watcher.

    This marks the end of the active watch phase.
    """

    resume: asyncio.Event = attrs.field(factory=asyncio.Event)
    """Event set when the watcher should resume activity."""

    deleted: set[Path] = attrs.field(init=False, factory=set)
    """Files deleted while the watcher is active.

    These changes are sent to the workflow at the end of the watch phase.
    """

    updated: set[Path] = attrs.field(init=False, factory=set)
    """Files changed or added files while the watcher is active.

    These changes are sent to the workflow at the end of the watch phase.
    """

    files_changed_events: set[asyncio.Event] = attrs.field(init=False, factory=set)
    """Event set to True when a relevant file event was recorded.

    This is used by the watch_update and watch_delete functions.
    """

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
        # Reset the state of the watcher: changes are not processed yet.
        # Other parts of StepUp can wait for file changes.
        self.processed.clear()
        for event in self.files_changed_events:
            event.clear()

        # Process changes to static files picked up during run phase.
        await self.reporter("PHASE", "watch")
        while not change_queue.empty():
            change, path = change_queue.get_nowait()
            file = self.workflow.find(File, path)
            if file is not None and file.get_state() == FileState.STATIC:
                await self.record_change(change, path)

        # Wait for new changes to show up.
        self.active.set()
        async for change, path in stoppable_iterator(change_queue.get, self.interrupt):
            await self.record_change(change, path)

        # Feed all updates to the worker and clean up.
        self.active.clear()
        async with self.dblock:
            # Update the hashes of all files known to the workflow.
            old_hashes = self.workflow.get_file_hashes(self.updated | self.deleted)
            new_hashes = []
            for path, old_file_hash in old_hashes:
                new_file_hash = old_file_hash.regen(path)
                if new_file_hash == old_file_hash:
                    await self.reporter("UNCHANGED", path)
                    self.updated.discard(path)
                else:
                    new_hashes.append((path, new_file_hash))
            if len(new_hashes) > 0:
                self.workflow.update_file_hashes(new_hashes, "external")

            # Mark steps pending if they use nglob patterns that have different matches.
            self.workflow.process_nglob_changes(self.deleted, self.updated)

            # Queue all pending steps.
            self.workflow.queue_pending_steps()

        # Reset the watcher state.
        self.deleted.clear()
        self.updated.clear()
        for event in self.files_changed_events:
            event.clear()
        self.interrupt.clear()
        self.processed.set()

    async def record_change(self, change: Change, path: Path):
        """Record a single event taken from the change_queue."""
        if change == Change.DELETED and path not in self.deleted:
            if self.workflow.is_relevant(path):
                await self.reporter("DELETED", path)
                self.deleted.add(path)
                self.updated.discard(path)
                for event in self.files_changed_events:
                    event.set()
        elif change == Change.UPDATED and path not in self.updated:  # noqa: SIM102
            if self.workflow.is_relevant(path):
                await self.reporter("UPDATED", path)
                self.deleted.discard(path)
                self.updated.add(path)
                for event in self.files_changed_events:
                    event.set()
                # When a directory is added, create a watcher early,
                # to catch events in this directory.
                # All files already present are also considered to be updated.
                if path.endswith("/"):
                    self.dir_queue.put_nowait((DirWatch.START, path))
                    for sub_path in path.iterdir():
                        if sub_path.is_dir():
                            sub_path = sub_path / ""
                        await self.record_change(Change.UPDATED, sub_path)


@attrs.define
class AsyncInotifyWrapper:
    """Interface between a `Watcher` instance and the `asyncinotify` library."""

    dir_queue: asyncio.Queue = attrs.field()
    """The dir_queue provides directories to (un)watch.

    Each item is a tuple `(dir_watch, path)`,
    where `dir_watch` is an instance of `DirWatch`
    to specify whether to start or stop watching a directory.
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
        async for dir_watch, path in stoppable_iterator(self.dir_queue.get, self.stop_event):
            if dir_watch == DirWatch.STOP:
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
            change = (
                Change.DELETED
                if event.mask & (Mask.DELETE | Mask.DELETE_SELF | Mask.MOVED_FROM | Mask.MOVE_SELF)
                else Change.UPDATED
            )
            path = Path(event.path)
            if event.mask & Mask.ISDIR:
                path = path / ""
            self.change_queue.put_nowait((change, path))
