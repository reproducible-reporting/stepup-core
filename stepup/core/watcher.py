# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Watch for file changes and update the workflow accordingly."""

import asyncio
import logging
import sys

import attrs
from path import Path

from .asyncio import iter_until_stopped, wait_for_any_event
from .enums import Change, HashUpdateCause
from .executor import Executor
from .hash_queue import HashQueue, gather_hashes
from .reporter import ReporterClient
from .sqlite3 import DBSession
from .workflow import Workflow

WATCHER_AVAILABLE = sys.platform == "linux"
if WATCHER_AVAILABLE:
    from asyncinotify import Inotify, Mask, Watch
else:
    # Dummy classes for non-Linux platforms to avoid type errors below.
    class Inotify:
        pass

    class Mask:
        pass

    class Watch:
        pass


__all__ = ("WATCHER_AVAILABLE", "Watcher")


logger = logging.getLogger(__name__)


@attrs.define
class Watcher:
    """Watch for file changes and update the workflow accordingly.

    Changes are sent to the workflow at the end of the watch phase, before the build phase.
    """

    workflow: Workflow = attrs.field()
    """The workflow to report file events to."""

    db: DBSession = attrs.field()
    """The workflow database session, i.e. the same object as `workflow.db`."""

    reporter: ReporterClient = attrs.field()
    """The reporter to send progress information to."""

    dir_queue: asyncio.Queue = attrs.field()
    """Queue to receive directories to watch for file events.

    The current implementation can only start watching new directories,
    and does not support stopping watching directories.
    """

    executor: Executor = attrs.field()
    """Runs the hash jobs submitted through `hash_queue`, one thread per file."""

    hash_queue: HashQueue = attrs.field()
    """Where file hashes to (re)compute are submitted, shared with `Builder`.

    This must be the same instance as `Builder.hash_queue`,
    because a submission also sets the queue's `wake` event,
    which is how a parked builder job loop is nudged.
    """

    njob: int = attrs.field()
    """Maximum number of hash jobs to run concurrently while draining `hash_queue` directly.

    The builder's job loop is not running during the watch phase,
    so this bounds the concurrency in its place.
    Mirrors `Builder.njob`.
    """

    active: asyncio.Event = attrs.field(factory=asyncio.Event)
    """The active event is set when the Watcher is reporting file system events.

    It always watches for changes, but only reports them when active.
    """

    processed: asyncio.Event = attrs.field(factory=asyncio.Event)
    """The processed event is set when the Watcher has passed all information to the workflow.

    After this event is set, the build phase can start.
    """

    interrupt: asyncio.Event = attrs.field(factory=asyncio.Event)
    """Event set when other parts of StepUp want to interrupt the watcher.

    This marks the end of the active watch phase.
    """

    resume: asyncio.Event = attrs.field(factory=asyncio.Event)
    """Event set when the watcher should resume activity."""

    deleted: set[Path] = attrs.field(init=False, factory=set)
    """Files deleted while the watcher is active."""

    updated: set[Path] = attrs.field(init=False, factory=set)
    """Files created or modified while the watcher is active."""

    files_changed_events: set[asyncio.Event] = attrs.field(init=False, factory=set)
    """Events registered by callers waiting for the next relevant file change.

    Every event in this set is set() whenever a relevant change is recorded.
    A waiter adds its own event before waiting and discards it afterward.
    """

    async def loop(self, stop_event: asyncio.Event):
        """Run the main watcher loop.

        Parameters
        ----------
        stop_event
            The main watcher loop is interrupted by this event.

        Notes
        -----
        One iteration of the main watcher loop consists of observing multiple file events.
        The iteration ends by informing the workflow of all the changes,
        after which StepUp starts the builder again (or exits).
        """
        async with AsyncInotifyWrapper(self.dir_queue) as wrapper:
            while not stop_event.is_set():
                await wait_for_any_event(self.resume, stop_event, wrapper.stop_event)
                if stop_event.is_set() or wrapper.stop_event.is_set():
                    break
                await self.watch_changes(wrapper.change_queue)
                self.resume.clear()

    async def watch_changes(self, change_queue: asyncio.Queue):
        """Watch file events.

        The observed changes are sent to the workflow right before the next build phase.
        """
        # Reset the state of the watcher: changes are not processed yet.
        # Other parts of StepUp can wait for file changes.
        self.processed.clear()
        for event in self.files_changed_events:
            event.clear()

        # Process changes to static files picked up during the build phase.
        await self.reporter("PHASE", "watch")
        async with self.db:
            while not change_queue.empty():
                change, path = change_queue.get_nowait()
                if self.workflow.change_is_relevant_during_build(path):
                    await self.record_change(change, path)

        # Wait for new changes to show up.
        # The lock is acquired inside the loop because the loop itself is long-running.
        self.active.set()
        async for change, path in iter_until_stopped(change_queue.get, self.interrupt):
            async with self.db:
                await self.record_change(change, path)

        # Feed all updates to the workflow and clean up.
        self.active.clear()
        async with self.db:
            old_hashes = self.workflow.get_file_hashes(self.updated | self.deleted)

        # Hashing runs outside any held transaction.
        # Each hash job applies its own result in its own short transaction,
        # which is safe here because no build phase is active to contend with.
        new_hashes = await gather_hashes(
            self.hash_queue,
            self.executor,
            self.reporter,
            [(path, old_hash, HashUpdateCause.EXTERNAL) for path, old_hash in old_hashes.items()],
            self.njob,
        )

        async with self.db:
            # An unchanged result was deliberately not applied by the hash job itself:
            # report it instead, and prune it from self.updated before process_nglob_changes runs,
            # so an unchanged file does not count as an nglob change.
            for path, new_file_hash in new_hashes.items():
                if new_file_hash == old_hashes[path]:
                    await self.reporter("UNCHANGED", path)
                    self.updated.discard(path)

            # Mark steps pending if they use nglob patterns that have different matches.
            self.workflow.process_nglob_changes(self.deleted, self.updated)

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
            if self.workflow.change_is_relevant(path):
                await self.reporter("DELETED", path)
                self.deleted.add(path)
                self.updated.discard(path)
                for event in self.files_changed_events:
                    event.set()
        elif change == Change.UPDATED and path not in self.updated:
            if self.workflow.change_is_relevant(path):
                await self.reporter("UPDATED", path)
                self.deleted.discard(path)
                self.updated.add(path)
                for event in self.files_changed_events:
                    event.set()
        elif change == Change.DELETED_PARENT:
            for sub_path in self.workflow.relevant_paths_under(path):
                if sub_path not in self.deleted:
                    await self.reporter("DELETED", sub_path)
                    self.deleted.add(sub_path)
                    self.updated.discard(sub_path)
                    for event in self.files_changed_events:
                        event.set()


@attrs.define
class AsyncInotifyWrapper:
    """Interface between a `Watcher` instance and the `asyncinotify` library."""

    dir_queue: asyncio.Queue = attrs.field()
    """The dir_queue provides directories to watch.

    Only new watches can be installed. Existing watches cannot be removed,
    but will be removed automatically when the directory is deleted.
    """

    inotify: Inotify | None = attrs.field(init=False, default=None)
    """Inotify object, only present while the context is open."""

    stop_event: asyncio.Event = attrs.field(init=False, factory=asyncio.Event)
    """Internal stop event, set when the context is closed."""

    watches: dict[Path, Watch | None] = attrs.field(init=False, factory=dict)
    """Watches created with asyncinotify, keyed by directory."""

    change_queue: asyncio.Queue = attrs.field(init=False, factory=asyncio.Queue)
    """A queue object holding file changes received from asyncinotify.

    Each item is a tuple with a `Change` instance and a path.
    """

    dir_loop_task: asyncio.Task | None = attrs.field(init=False, default=None)
    """Task corresponding to the dir_loop method."""

    change_loop_task: asyncio.Task | None = attrs.field(init=False, default=None)
    """Task corresponding to the change_loop method."""

    async def __aenter__(self):
        """Start the `AsyncInotifyWrapper`."""
        self.inotify = Inotify()
        self.stop_event.clear()
        self.dir_loop_task = asyncio.create_task(
            self.dir_loop(), name="AsyncInotifyWrapper.dir_loop"
        )
        self.change_loop_task = asyncio.create_task(
            self.change_loop(), name="AsyncInotifyWrapper.change_loop"
        )
        self.dir_loop_task.add_done_callback(self._signal_stop_on_error)
        self.change_loop_task.add_done_callback(self._signal_stop_on_error)
        return self

    async def __aexit__(self, exc_type, exc, tb):
        """Close the `AsyncInotifyWrapper`."""
        try:
            self.stop_event.set()
            await asyncio.gather(self.dir_loop_task, self.change_loop_task)
        finally:
            self.dir_loop_task = None
            self.change_loop_task = None
            if self.inotify is not None:
                self.inotify.close()
            self.inotify = None

    def _signal_stop_on_error(self, task: asyncio.Task):
        """Wake the parent loop when a background task fails."""
        if task.cancelled():
            logger.info("Inotify task %s cancelled, stopping watcher", task.get_name())
            return
        exception = task.exception()
        if exception is not None:
            logger.error("Inotify task %s raised an exception: %s", task.get_name(), exception)
            self.stop_event.set()

    async def dir_loop(self):
        """Install a watch for every directory received from the dir_queue.

        The file events observed in a watched directory are put on the change_queue.
        A directory that does not exist yet is recorded without a watch,
        just like one that was watched before and has been removed since.
        Its nearest existing ancestor is watched instead,
        so `change_loop` sees it appear and installs the pending watch at that moment.
        """
        async for path in iter_until_stopped(self.dir_queue.get, self.stop_event):
            path = Path(path).normpath()
            while not (path.is_dir() or path.name == ".." or path in ("", ".")):
                self.watches.setdefault(path, None)
                path = path.parent
            while path.name != "..":
                if path == "":
                    path = Path(".")
                if self.watches.get(path) is not None:
                    break
                self._install_watch(path)
                if path == ".":
                    break
                path = path.parent

    async def change_loop(self):
        """Collect events from inotify and translate them into items for the change_queue."""
        async for event in iter_until_stopped(self.inotify.get, self.stop_event):
            # Mark watches that inotify reports as removed
            path = Path(event.path)
            if event.mask & Mask.IGNORED:
                self.watches[path] = None
                continue
            # Determine the type of change
            change = (
                Change.DELETED
                if event.mask & (Mask.DELETE | Mask.DELETE_SELF | Mask.MOVED_FROM | Mask.MOVE_SELF)
                else Change.UPDATED
            )
            logger.debug("Received inotify event: %s %s", change.name, event.path)
            if event.mask & Mask.ISDIR:
                # For directories, we only care about updating the inotify watches.
                if change == Change.DELETED:
                    # Unset the watch for the directory.
                    # We do not remove it from the watches dict,
                    # so we can check for it when the directory reappears.
                    watch = self.watches.get(path)
                    if watch is not None:
                        self.inotify.rm_watch(watch)
                        self.watches[path] = None
                        self.change_queue.put_nowait((Change.DELETED_PARENT, path))
                else:
                    paths = [path]
                    while len(paths) > 0:
                        path = paths.pop(0)
                        watch = self.watches.get(path, "default")
                        if watch is None:
                            # When a directory is added that was once watched,
                            # recreate the watch right away.
                            self._install_watch(path)
                        if watch != "default":
                            # Events of files created in this directory may have been missed.
                            for sub_path in path.iterdir():
                                if sub_path.is_file():
                                    self.change_queue.put_nowait((Change.UPDATED, sub_path))
                                elif sub_path.is_dir():
                                    paths.append(sub_path)
            else:
                self.change_queue.put_nowait((change, path))

    def _install_watch(self, path: str):
        self.watches[Path(path)] = self.inotify.add_watch(
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
