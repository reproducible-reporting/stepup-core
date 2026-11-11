# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Watch the file system with the `asyncinotify` library, which is only available on Linux."""

import asyncio
import logging

import attrs
from asyncinotify import Inotify, Mask, Watch

from .asyncio import iter_until_stopped
from .enums import Change
from .path import Path
from .watcher_backend import WatcherBackend

__all__ = ("InotifyBackend",)


logger = logging.getLogger(__name__)


@attrs.define
class InotifyBackend(WatcherBackend):
    """Interface between a `Watcher` instance and the `asyncinotify` library."""

    inotify: Inotify | None = attrs.field(init=False, default=None)
    """Inotify object, only present while the context is open."""

    watches: dict[Path, Watch | None] = attrs.field(init=False, factory=dict)
    """Watches created with asyncinotify, keyed by directory."""

    async def __aenter__(self):
        """Start the `InotifyBackend`."""
        self.inotify = Inotify()
        self.stop_event.clear()
        self.dir_loop_task = asyncio.create_task(self.dir_loop(), name="InotifyBackend.dir_loop")
        self.change_loop_task = asyncio.create_task(
            self.change_loop(), name="InotifyBackend.change_loop"
        )
        self.dir_loop_task.add_done_callback(self._signal_stop_on_error)
        self.change_loop_task.add_done_callback(self._signal_stop_on_error)
        return self

    async def __aexit__(self, exc_type, exc, tb):
        """Close the `InotifyBackend`."""
        try:
            self.stop_event.set()
            await asyncio.gather(self.dir_loop_task, self.change_loop_task)
        finally:
            self.dir_loop_task = None
            self.change_loop_task = None
            if self.inotify is not None:
                self.inotify.close()
            self.inotify = None

    async def flush(self):
        """Wait until the changes made before this call are on the change_queue.

        This does nothing, because `change_loop` reads the inotify events
        as soon as the event loop sees them arrive.
        """

    async def dir_loop(self):
        """Install a watch for every directory received from the dir_queue."""
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
                        if path not in self.watches:
                            continue
                        if self.watches[path] is None:
                            # When a directory is added that was once watched,
                            # recreate the watch right away.
                            self._install_watch(path)
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
