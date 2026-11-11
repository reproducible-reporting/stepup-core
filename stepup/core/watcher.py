# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Watch for file changes and update the workflow accordingly.

`Watcher` runs a single **watch phase** per `run_once()` call:
it waits for changes reported by the operating system,
records the relevant ones,
and ends by feeding them all to the workflow at once,
after which the next build phase can start.
The phase is bracketed by four events:
`start_watching` and `end_watching` are the commands other parts of StepUp use to drive it,
`busy_watching` and `done_watching` are how it reports where it is.
Waiters that need to know about individual changes as they come in
subscribe with `Watcher.subscribe_changes`.

Everything that talks to a file watching library is isolated in a `WatcherBackend`,
see `stepup.core.watcher_backend`.
`InotifyBackend` (`stepup.core.watcher_inotify`) uses the `asyncinotify` library,
and `WatchfilesBackend` (`stepup.core.watcher_watchfiles`) uses the `watchfiles` library.
`select_watcher_backend` decides which of the two is used.
"""

import asyncio
import contextlib
import importlib.util
import logging
import os
import sys
from collections.abc import Generator

import attrs
from path import Path

from .asyncio import iter_until_stopped, wait_for_any_event
from .enums import Change, HashUpdateCause
from .exceptions import ConfigError
from .executor import Executor
from .hash_queue import HashQueue, gather_hashes
from .reporter import ReporterClient
from .sqlite3 import DBSession
from .watcher_backend import WatcherBackend
from .watcher_watchfiles import WatchfilesBackend
from .workflow import Workflow


def select_watcher_backend() -> str:
    """Select the library that watches the file system in watch mode.

    On Linux, `asyncinotify` is used by default,
    and `STEPUP_WATCHER=watchfiles` selects the `watchfiles` library instead,
    which is an optional dependency on this platform.
    On other platforms, `watchfiles` is the only choice.

    Returns
    -------
    backend
        The name of the library, `asyncinotify` or `watchfiles`.

    Raises
    ------
    ConfigError
        If `STEPUP_WATCHER` names a library that cannot be used on this platform,
        or if the selected library is not installed.
    """
    choices = ("asyncinotify", "watchfiles") if sys.platform == "linux" else ("watchfiles",)
    backend = os.environ.get("STEPUP_WATCHER", "") or choices[0]
    if backend not in choices:
        raise ConfigError(
            f"STEPUP_WATCHER must be {' or '.join(choices)} on {sys.platform}, got {backend!r}."
        )
    if importlib.util.find_spec(backend) is None:
        raise ConfigError(f"Watch mode needs the {backend} library, which is not installed.")
    return backend


def _import_backend_class(backend: str) -> type[WatcherBackend]:
    """Return the class of the backend that `select_watcher_backend` has selected.

    The module of the `asyncinotify` backend is only imported when it is selected,
    because the `asyncinotify` library is only installed on Linux.
    """
    if backend == "watchfiles":
        return WatchfilesBackend
    return importlib.import_module(".watcher_inotify", __package__).InotifyBackend


__all__ = ("Watcher", "select_watcher_backend")


logger = logging.getLogger(__name__)


@attrs.define
class Watcher:
    """Watch for file changes and update the workflow accordingly.

    Changes are sent to the workflow at the end of the watch phase, before the build phase.
    """

    workflow: Workflow = attrs.field(kw_only=True)
    """The workflow to report file events to."""

    db: DBSession = attrs.field(kw_only=True)
    """The workflow database session, i.e. the same object as `workflow.db`."""

    reporter: ReporterClient = attrs.field(kw_only=True)
    """The reporter to send progress information to."""

    dir_queue: asyncio.Queue[Path] = attrs.field(kw_only=True)
    """Queue to receive directories to watch for file events.

    It is handed to the `WatcherBackend`, which installs the watches.
    """

    executor: Executor = attrs.field(kw_only=True)
    """Runs the hash jobs submitted through `hash_queue`, one thread per file."""

    hash_queue: HashQueue = attrs.field(kw_only=True)
    """Where file hashes to (re)compute are submitted, shared with `Builder`.

    This must be the same instance as `Builder.hash_queue`,
    because a submission also sets the queue's `wake` event,
    which is how a parked builder job loop is nudged.
    """

    njob: int = attrs.field(kw_only=True)
    """Maximum number of hash jobs to run concurrently while draining `hash_queue` directly.

    The builder's job loop is not running during the watch phase,
    so this bounds the concurrency in its place.
    Mirrors `Builder.njob`.
    """

    busy_watching: asyncio.Event = attrs.field(init=False, factory=asyncio.Event)
    """Set while the watcher is reporting file system events.

    It always watches for changes, but only reports them while this event is set.
    """

    done_watching: asyncio.Event = attrs.field(init=False, factory=asyncio.Event)
    """Set when the watcher has passed all information to the workflow.

    After this event is set, the build phase can start.
    """

    end_watching: asyncio.Event = attrs.field(init=False, factory=asyncio.Event)
    """Set when other parts of StepUp want the watch phase to end.

    The watcher then stops collecting changes and commits the ones it has.
    """

    start_watching: asyncio.Event = attrs.field(init=False, factory=asyncio.Event)
    """Set when other parts of StepUp want a new watch phase to begin."""

    deleted: set[str] = attrs.field(init=False, factory=set)
    """Files deleted while the watcher is active."""

    updated: set[str] = attrs.field(init=False, factory=set)
    """Files created or modified while the watcher is active."""

    files_changed_events: set[asyncio.Event] = attrs.field(init=False, factory=set)
    """The events of the subscribers waiting for the next relevant file change.

    Every event in this set is set() whenever a relevant change is recorded.
    Use `subscribe_changes` to add and remove one.
    """

    @contextlib.contextmanager
    def subscribe_changes(self) -> Generator[asyncio.Event]:
        """Provide an event that is set whenever a relevant file change is recorded.

        The event is cleared at the end of a watch phase,
        together with the `deleted` and `updated` sets it refers to.
        A subscriber that waits more than once clears the event itself after each wait.
        """
        event = asyncio.Event()
        self.files_changed_events.add(event)
        try:
            yield event
        finally:
            self.files_changed_events.discard(event)

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
        backend_class = _import_backend_class(select_watcher_backend())
        async with backend_class(dir_queue=self.dir_queue) as backend:
            while not stop_event.is_set():
                await wait_for_any_event(self.start_watching, stop_event, backend.stop_event)
                if stop_event.is_set() or backend.stop_event.is_set():
                    break
                # The changes made during the build phase must have arrived,
                # so they are recorded as such.
                await backend.flush()
                await self.run_once(backend.change_queue)
                self.start_watching.clear()

    async def run_once(self, change_queue: asyncio.Queue[tuple[Change, Path]]):
        """Run a single watch phase.

        The observed changes are sent to the workflow right before the next build phase.
        """
        # The changes of this phase are not processed yet.
        self.done_watching.clear()

        # Process changes to static files picked up during the build phase.
        await self.reporter("PHASE", "watch")
        async with self.db:
            while not change_queue.empty():
                change, path = change_queue.get_nowait()
                await self.record_change(change, path, during_build=True)

        # Wait for new changes to show up.
        # The lock is acquired inside the loop because the loop itself is long-running.
        self.busy_watching.set()
        async for change, path in iter_until_stopped(change_queue.get, self.end_watching):
            async with self.db:
                await self.record_change(change, path)

        # Feed all updates to the workflow and clean up.
        self.busy_watching.clear()
        async with self.db:
            old_hashes = self.workflow.get_observable_file_hashes(self.updated | self.deleted)

        # Hashing runs outside any held transaction,
        # so the whole batch can be applied at once below.
        new_hashes = await gather_hashes(
            self.hash_queue,
            self.executor,
            self.reporter,
            list(old_hashes.items()),
            self.njob,
        )

        async with self.db:
            # A dropped path is pruned from self.updated,
            # so an unchanged file does not count as an nglob change below.
            dropped = self.workflow.update_file_hashes(new_hashes, cause=HashUpdateCause.OBSERVED)
            self.updated.difference_update(dropped)

            # Mark steps pending if they use nglob patterns that have different matches.
            self.workflow.process_nglob_changes(self.deleted, self.updated)

        # The reporter is an RPC client to the terminal user interface,
        # so its round trips are made after the transaction closes,
        # instead of holding the database write lock for the duration of the network traffic.
        for path in dropped:
            await self.reporter("SAMEHASH", path)

        # Reset the watcher state.
        # The subscriber events are cleared together with the sets they refer to,
        # so a subscriber never wakes up for changes that are no longer recorded.
        self.deleted.clear()
        self.updated.clear()
        for event in self.files_changed_events:
            event.clear()
        self.end_watching.clear()
        self.done_watching.set()

    async def record_change(self, change: Change, path: Path, *, during_build: bool = False):
        """Record a single file system change, if it is relevant to the workflow.

        Parameters
        ----------
        change
            The kind of change observed.
        path
            The file that changed,
            or, for `Change.DELETED_PARENT`, the directory that was removed.
        during_build
            Whether the change was observed while a build phase was running.
            The build is writing its own outputs then,
            so only a change to a static file counts as news.
        """
        if change == Change.DELETED and path not in self.deleted:
            if self.workflow.change_is_relevant(path, during_build=during_build):
                await self.reporter("DELETED", path)
                self.deleted.add(path)
                self.updated.discard(path)
                for event in self.files_changed_events:
                    event.set()
        elif change == Change.UPDATED and path not in self.updated:
            if self.workflow.change_is_relevant(path, during_build=during_build):
                await self.reporter("UPDATED", path)
                self.deleted.discard(path)
                self.updated.add(path)
                for event in self.files_changed_events:
                    event.set()
        elif change == Change.DELETED_PARENT:
            for sub_path in self.workflow.relevant_paths_under(path, during_build=during_build):
                if sub_path not in self.deleted:
                    await self.reporter("DELETED", sub_path)
                    self.deleted.add(sub_path)
                    self.updated.discard(sub_path)
                    for event in self.files_changed_events:
                        event.set()
