# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Watch the file system with the `watchfiles` library."""

import asyncio
import concurrent.futures
import functools
import importlib
import os
import queue
import stat
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING

import attrs
from path import Path

from .asyncio import iter_until_stopped, wait_for_any_event
from .enums import Change
from .watcher_backend import WatcherBackend

if TYPE_CHECKING:
    from watchfiles._rust_notify import RustNotify

__all__ = ("WatchfilesBackend",)


WATCHFILES_STEP_MS = 10
"""The polling interval of `RustNotify.watch`, in milliseconds.

A batch of changes is returned as soon as it has not grown during one interval.
"""

WATCHFILES_DEBOUNCE_MS = 100
"""The longest time that `RustNotify.watch` keeps growing a batch of changes, in milliseconds."""

WATCHFILES_TIMEOUT_MS = 50
"""How long `RustNotify.watch` waits for a first change before it returns, in milliseconds.

The reader thread of `WatchfilesBackend` only replaces a generation in between two calls,
so this bounds the delay before new watches take effect.
"""

WATCHFILES_MAX_ATTEMPTS = 10
"""How many times to try creating a `RustNotify` instance while watched directories vanish."""

_ADDED = 1
"""The code of a created path in a batch from `RustNotify.watch`, see `watchfiles.Change`."""

_MODIFIED = 2
"""The code of a modified path in a batch from `RustNotify.watch`, see `watchfiles.Change`."""

_DELETED = 3
"""The code of a removed path in a batch from `RustNotify.watch`, see `watchfiles.Change`."""


@functools.cache
def _import_rust_notify() -> type["RustNotify"]:
    """Import the class through which `WatchfilesBackend` uses the `watchfiles` library.

    The import is postponed until watch mode needs it,
    because importing watchfiles takes about 0.15 seconds
    (watchfiles 1.3.0, mostly spent on anyio).
    """
    return importlib.import_module("watchfiles._rust_notify").RustNotify


@attrs.define
class WatchfilesBackend(WatcherBackend):
    """Interface between a `Watcher` instance and the `watchfiles` library.

    It behaves as described for `WatcherBackend`, except for two details,
    which are due to watchfiles handing over changes as an unordered set (as of watchfiles 1.3.0):

    - The changes observed within one batch (about 10 milliseconds)
      are reported in a fixed order instead of the order in which they happened.
    - When the changes of a path within one batch contradict each other,
      the current state of the path decides what is reported.
      For example, a file that was created and removed within one batch
      is only reported as removed.

    watchfiles cannot add paths to a running watcher (as of watchfiles 1.3.0).
    Each time new directories must be watched,
    a new `RustNotify` instance, called a generation, is created to watch all of them.
    One thread owns all instances, reads their changes and replaces them on request.
    """

    watches: dict[Path, int | None] = attrs.field(init=False, factory=dict)
    """The directories to watch, with the number of the first generation that watches them.

    Changes reported by an older generation are stale and ignored.
    """

    subdirs: dict[Path, set[str]] = attrs.field(init=False, factory=dict)
    """The names of the subdirectories of each watched directory, symbolic links excluded.

    watchfiles does not tell whether a removed path was a directory,
    so it is looked up here.
    """

    _pending: dict[Path, bool] = attrs.field(init=False, factory=dict)
    """The directories to watch in the next generation.

    The value tells whether the files in the directory must be reported once it is watched,
    which is the case for a directory that has appeared since StepUp asked to watch it.
    """

    _installing: dict[Path, bool] = attrs.field(init=False, factory=dict)
    """Like `_pending`, for the generation that is being created."""

    _generation: int = attrs.field(init=False, default=0)
    """The number of the latest generation requested from the reader thread."""

    _real_paths: dict[Path, str] = attrs.field(init=False, factory=dict)
    """The path given to `RustNotify` for each watched directory."""

    _rebuild_needed: asyncio.Event = attrs.field(init=False, factory=asyncio.Event)
    """Set when directories have been added to `_pending`."""

    _rebuild_idle: asyncio.Event = attrs.field(init=False, factory=asyncio.Event)
    """Set when no directories are pending or being installed."""

    _installed: asyncio.Event = attrs.field(init=False, factory=asyncio.Event)
    """Set when the latest generation has started watching."""

    _commands: queue.SimpleQueue = attrs.field(init=False, factory=queue.SimpleQueue)
    """Requests for new generations, sent to the reader thread. `None` stops the thread."""

    _work: asyncio.Queue[Callable[[], None]] = attrs.field(init=False, factory=asyncio.Queue)
    """Work handed over by the reader thread, carried out by `change_loop` in the same order."""

    _closing: threading.Event = attrs.field(init=False, factory=threading.Event)
    """Set when the reader thread must stop."""

    _flush_requested: int = attrs.field(init=False, default=0)
    """The number of calls to `flush` so far."""

    _flush_sent: int = attrs.field(init=False, default=0)
    """The latest flush request that the reader thread has completed.

    Only the reader thread uses this attribute.
    """

    _flush_done: int = attrs.field(init=False, default=0)
    """The latest flush request whose changes are all on the change_queue."""

    _flush_event: asyncio.Event = attrs.field(init=False, factory=asyncio.Event)
    """Set when `_flush_done` increases."""

    _executor: concurrent.futures.ThreadPoolExecutor | None = attrs.field(init=False, default=None)
    """The executor that runs the reader thread.

    The reader thread runs as long as the backend does,
    and would otherwise permanently take a worker from the default executor,
    which also waits for the subprocesses of steps.
    """

    read_future: asyncio.Future | None = attrs.field(init=False, default=None)
    """Future corresponding to the `_read_loop` method, running in the reader thread."""

    rebuild_loop_task: asyncio.Task | None = attrs.field(init=False, default=None)
    """Task corresponding to the rebuild_loop method."""

    async def __aenter__(self):
        """Start the `WatchfilesBackend`."""
        # Import here rather than in the reader thread, where a failure would surface late.
        _import_rust_notify()
        loop = asyncio.get_running_loop()
        self.stop_event.clear()
        self._closing.clear()
        self._executor = concurrent.futures.ThreadPoolExecutor(1, thread_name_prefix="watchfiles")
        self.read_future = loop.run_in_executor(self._executor, self._read_loop, loop)
        self.dir_loop_task = asyncio.create_task(self.dir_loop(), name="WatchfilesBackend.dir_loop")
        self.change_loop_task = asyncio.create_task(
            self.change_loop(), name="WatchfilesBackend.change_loop"
        )
        self.rebuild_loop_task = asyncio.create_task(
            self.rebuild_loop(), name="WatchfilesBackend.rebuild_loop"
        )
        for future in self._futures():
            future.add_done_callback(self._signal_stop_on_error)
        return self

    async def __aexit__(self, exc_type, exc, tb):
        """Close the `WatchfilesBackend`."""
        self.stop_event.set()
        self._closing.set()
        self._commands.put(None)
        try:
            # Wait for all of them, so the reader thread has stopped before anything is raised.
            results = await asyncio.gather(*self._futures(), return_exceptions=True)
        finally:
            self._executor.shutdown(wait=False)
            self._executor = None
            self.read_future = None
            self.dir_loop_task = None
            self.change_loop_task = None
            self.rebuild_loop_task = None
        for result in results:
            if isinstance(result, BaseException):
                raise result

    def _futures(self) -> list[asyncio.Future]:
        """Return the futures of the reader thread and the background tasks."""
        return [self.read_future, self.dir_loop_task, self.change_loop_task, self.rebuild_loop_task]

    async def flush(self):
        """Wait until the changes made before this call are on the change_queue.

        watchfiles delivers changes in batches, so they arrive a little later than they happen.
        The directories waiting for a watch are watched first,
        because a new watch may report the files already present in its directory.
        """
        while not self.stop_event.is_set():
            await wait_for_any_event(self._rebuild_idle, self.stop_event)
            await self._flush_reader()
            # The changes may have revealed more directories to watch.
            if self._rebuild_idle.is_set():
                break

    async def _flush_reader(self):
        """Wait until the reader thread has handed over the changes made before this call."""
        if self._generation == 0:
            # Nothing is watched.
            return
        self._flush_requested += 1
        request = self._flush_requested
        while self._flush_done < request and not self.stop_event.is_set():
            self._flush_event.clear()
            await wait_for_any_event(self._flush_event, self.stop_event)

    def _complete_flush(self, request: int):
        """Record that all changes made before a flush request are on the change_queue."""
        self._flush_done = request
        self._flush_event.set()

    async def dir_loop(self):
        """Schedule a watch for every directory received from the dir_queue."""
        async for path in iter_until_stopped(self.dir_queue.get, self.stop_event):
            path = Path(path).normpath()
            while not (path.is_dir() or path.name == ".." or path in ("", ".")):
                self.watches.setdefault(path, None)
                path = path.parent
            while path.name != "..":
                if path == "":
                    path = Path(".")
                if self.watches.get(path) is not None or self._is_scheduled(path):
                    break
                self.watches[path] = None
                self._schedule(path, report_files=False)
                if path == ".":
                    break
                path = path.parent

    async def change_loop(self):
        """Carry out the work handed over by the reader thread, in the order it was sent."""
        async for work in iter_until_stopped(self._work.get, self.stop_event):
            work()

    async def rebuild_loop(self):
        """Request a new generation from the reader thread whenever directories are pending.

        One generation is created at a time,
        so the directories that become pending in the meantime are watched together.
        """
        while True:
            if len(self._pending) == 0:
                self._rebuild_idle.set()
            await wait_for_any_event(self._rebuild_needed, self.stop_event)
            if self.stop_event.is_set():
                break
            self._rebuild_needed.clear()
            self._installing = self._pending
            self._pending = {}
            self._generation += 1
            keep = [
                (path, self._real_paths[path])
                for path, number in self.watches.items()
                if number is not None
            ]
            self._installed.clear()
            self._commands.put((self._generation, keep, list(self._installing)))
            await wait_for_any_event(self._installed, self.stop_event)

    def _read_loop(self, loop: asyncio.AbstractEventLoop):
        """Read the changes of the current generation, and replace it on request.

        This runs in the reader thread, which owns all `RustNotify` instances.
        """
        current = None
        try:
            while not self._closing.is_set():
                if current is None or not self._commands.empty():
                    command = self._commands.get()
                    if command is None:
                        break
                    generation, scans, missing = _create_generation(*command)
                    if current is not None:
                        # The old instance is drained after the new one has started,
                        # so every change is reported by at least one of them.
                        # A stop event would discard the changes that are not returned yet.
                        changes = current.notify.watch(
                            WATCHFILES_DEBOUNCE_MS, WATCHFILES_STEP_MS, WATCHFILES_STEP_MS, None
                        )
                        self._send_changes(loop, current, changes)
                        current.notify.close()
                    current = generation
                    self._send(
                        loop,
                        functools.partial(self._apply_generation, generation, scans, missing),
                    )
                else:
                    # A call that starts after a flush request returns all changes made before it,
                    # unless notify takes longer than one step to pass a change on.
                    request = self._flush_requested
                    flushing = request > self._flush_sent
                    changes = current.notify.watch(
                        WATCHFILES_DEBOUNCE_MS,
                        WATCHFILES_STEP_MS,
                        WATCHFILES_STEP_MS if flushing else WATCHFILES_TIMEOUT_MS,
                        self._closing,
                    )
                    self._send_changes(loop, current, changes)
                    if flushing:
                        self._flush_sent = request
                        self._send(loop, functools.partial(self._complete_flush, request))
        finally:
            if current is not None:
                current.notify.close()

    def _send(self, loop: asyncio.AbstractEventLoop, work: Callable[[], None]):
        """Hand work over from the reader thread to `change_loop`."""
        loop.call_soon_threadsafe(self._work.put_nowait, work)

    def _send_changes(self, loop: asyncio.AbstractEventLoop, generation: "_Generation", changes):
        """Hand the result of `RustNotify.watch` over to `change_loop`, if it has any changes.

        A result other than a set means that the call returned without changes.
        """
        if isinstance(changes, set) and len(changes) > 0:
            self._send(loop, functools.partial(self._process_changes, generation, changes))

    def _apply_generation(
        self, generation: "_Generation", scans: dict[Path, "_DirScan"], missing: list[Path]
    ):
        """Record the directories that a new generation watches.

        See `_create_generation` for the meaning of the arguments.
        """
        installing = self._installing
        self._installing = {}
        for path in missing:
            if self.watches.get(path) is not None:
                self._lose_dir(path)
            if path.is_dir():
                # It has been created again in the meantime.
                self._schedule_tree(path)
        for real_path, paths in generation.dirs.items():
            for path in paths:
                if path in scans:
                    self.watches[path] = generation.number
                    self._real_paths[path] = real_path
        for path, scan in scans.items():
            self._apply_scan(path, scan, report_files=installing[path])
        self._installed.set()

    def _process_changes(self, generation: "_Generation", changes: set[tuple[int, str]]):
        """Translate a batch of changes from `RustNotify.watch` into items for the change_queue.

        The watched directories in the batch are handled first,
        so that the removal of one of them stops the reporting of changes inside it.
        Within each group, the paths are handled in sorted order.
        Changes that came through a watch that has been dropped since are stale and ignored.
        """
        codes: dict[Path, set[int]] = {}
        sources: dict[Path, set[Path]] = {}
        for code, real_path in changes:
            parent, name = os.path.split(real_path)
            # A change inside a watched directory, or of a watched directory itself.
            pairs = [
                (directory, (directory / name).normpath())
                for directory in generation.dirs.get(parent, ())
            ]
            pairs.extend((directory, directory) for directory in generation.dirs.get(real_path, ()))
            for directory, path in pairs:
                codes.setdefault(path, set()).add(code)
                sources.setdefault(path, set()).add(directory)
        for path in sorted(codes, key=lambda path: (path not in self.watches, path)):
            if not any(self._watched_in(source, generation.number) for source in sources[path]):
                continue
            if path in self.watches:
                self._dir_changed(path, codes[path], generation.number)
            else:
                self._entry_changed(path, codes[path])

    def _watched_in(self, directory: Path, number: int) -> bool:
        """Return whether the current watch of a directory is part of the given generation."""
        first = self.watches.get(directory)
        return first is not None and first <= number

    def _dir_changed(self, path: Path, codes: set[int], number: int):
        """Handle the changes of a directory that must be watched."""
        if _DELETED in codes and self._watched_in(path, number):
            self._lose_dir(path)
        if path.is_dir():
            if self.watches[path] is None:
                self._schedule_tree(path)
            elif _ADDED in codes or _MODIFIED in codes:
                self._rescan(path)

    def _entry_changed(self, path: Path, codes: set[int]):
        """Handle the changes of a path that is not a watched directory.

        As with inotify, only the changes of files are reported, not those of directories.
        The codes of one batch may contradict each other,
        e.g. because FSEvents (macOS) reports all changes of a path over a short period,
        so the current state of the path decides what is reported.
        """
        known_dirs = self.subdirs.setdefault(_parent_dir(path), set())
        was_dir = path.name in known_dirs
        mode = _lstat_mode(path)
        is_dir = mode is not None and stat.S_ISDIR(mode)
        if is_dir:
            known_dirs.add(path.name)
        else:
            known_dirs.discard(path.name)
        if is_dir:
            # Only the file that the directory replaced is reported.
            changes = [Change.DELETED] if _DELETED in codes and not was_dir else []
        elif mode is None:
            if was_dir:
                changes = []
            elif _DELETED in codes:
                changes = [Change.DELETED]
            else:
                # The removal is reported in a later batch.
                changes = [Change.UPDATED]
        elif _DELETED in codes and not was_dir:
            changes = [Change.DELETED, Change.UPDATED]
        else:
            changes = [Change.UPDATED]
        for change in changes:
            self.change_queue.put_nowait((change, path))

    def _lose_dir(self, path: Path):
        """Forget the watches of a removed directory and of the directories below it."""
        for other in self.watches:
            if other == path or _is_under(other, path):
                self.watches[other] = None
                self.subdirs.pop(other, None)
        self.change_queue.put_nowait((Change.DELETED_PARENT, path))

    def _is_scheduled(self, path: Path) -> bool:
        """Return whether a watch of the directory has been requested."""
        return path in self._pending or path in self._installing

    def _schedule(self, path: Path, *, report_files: bool):
        """Watch a directory in the next generation, unless the one being created watches it.

        Parameters
        ----------
        path
            The directory to watch.
        report_files
            When `True`, all files in the directory are reported once it is watched.
        """
        if path in self._installing:
            self._installing[path] |= report_files
        else:
            self._pending[path] = self._pending.get(path, False) or report_files
            self._rebuild_idle.clear()
            self._rebuild_needed.set()

    def _schedule_tree(self, path: Path):
        """Watch a directory that has appeared, together with the directories to watch below it.

        All files in them are reported once they are watched,
        because they may have been created before.
        """
        self._schedule(path, report_files=True)
        for other, number in self.watches.items():
            if number is None and _is_under(other, path) and other.is_dir():
                self._schedule(other, report_files=True)

    def _apply_scan(self, path: Path, scan: "_DirScan", *, report_files: bool):
        """Record the entries of a watched directory, and report its files if requested."""
        self.subdirs[path] = scan.plain_dirs
        if report_files:
            for name in scan.files:
                self.change_queue.put_nowait((Change.UPDATED, (path / name).normpath()))
        for name in scan.dirs:
            sub_path = (path / name).normpath()
            # A directory to watch that exists but has no watch has appeared unnoticed.
            if (
                sub_path in self.watches
                and self.watches[sub_path] is None
                and (report_files or not self._is_scheduled(sub_path))
            ):
                self._schedule_tree(sub_path)

    def _rescan(self, path: Path):
        """Report all files in a watched directory and in the watched directories below it.

        This happens when a watched directory is created or changed,
        because changes of files created before the watch was installed may have been missed.
        """
        paths = [path]
        while len(paths) > 0:
            path = paths.pop(0)
            scan = _scan_dir(path)
            if scan is None:
                continue
            self._apply_scan(path, scan, report_files=True)
            for name in scan.dirs:
                sub_path = (path / name).normpath()
                if self.watches.get(sub_path) is not None:
                    paths.append(sub_path)


@attrs.define
class _Generation:
    """A `RustNotify` instance, together with the directories it watches."""

    number: int = attrs.field()
    """Sequence number, which tells the changes of a newer instance from those of an older one."""

    notify: "RustNotify" = attrs.field()
    """The instance itself."""

    dirs: dict[str, list[Path]] = attrs.field()
    """The watched directories, keyed by the path given to `RustNotify`.

    Symbolic links can lead to the same directory from more than one path,
    so each value is a list of directories as `WatchfilesBackend` knows them.
    """


@attrs.define(frozen=True)
class _DirScan:
    """The entries of a directory, each list sorted by name."""

    files: list[str] = attrs.field()
    """The names of the files, including symbolic links to files."""

    dirs: list[str] = attrs.field()
    """The names of the directories, including symbolic links to directories."""

    plain_dirs: set[str] = attrs.field()
    """The names of the directories that are not symbolic links."""


def _create_generation(
    number: int, keep: list[tuple[Path, str]], new: list[Path]
) -> tuple[_Generation, dict[Path, _DirScan], list[Path]]:
    """Create a `RustNotify` instance that watches the given directories.

    Parameters
    ----------
    number
        The number of the new generation.
    keep
        The directories that are watched already, with the paths given to `RustNotify` before.
    new
        The directories to watch from now on.

    Returns
    -------
    generation
        The new generation.
    scans
        The entries of the new directories, listed after their watches were installed.
    missing
        The directories that could not be watched, because they no longer exist.
    """
    real_paths = dict(keep)
    real_paths.update((path, os.path.realpath(path)) for path in new if path.is_dir())
    attempt = 1
    while True:
        dirs: dict[str, list[Path]] = {}
        for path, real_path in real_paths.items():
            dirs.setdefault(real_path, []).append(path)
        try:
            notify = _import_rust_notify()(
                watch_paths=list(dirs),
                debug=False,
                force_polling=False,
                poll_delay_ms=0,
                recursive=False,
                ignore_permission_denied=False,
            )
            break
        except FileNotFoundError:
            # A directory has been removed in the meantime,
            # which the generation that is still running reports.
            if attempt == WATCHFILES_MAX_ATTEMPTS:
                raise
            attempt += 1
            real_paths = {
                path: real_path
                for path, real_path in real_paths.items()
                if os.path.isdir(real_path)
            }
    scans = {}
    for path in new:
        if path in real_paths:
            scan = _scan_dir(path)
            if scan is not None:
                scans[path] = scan
    missing = [path for path, _ in keep if path not in real_paths]
    missing.extend(path for path in new if path not in scans)
    return _Generation(number, notify, dirs), scans, missing


def _scan_dir(path: Path) -> _DirScan | None:
    """List the entries of a directory, or return `None` when it no longer exists."""
    files = []
    dirs = []
    plain_dirs = set()
    try:
        with os.scandir(path) as entries:
            for entry in entries:
                if entry.is_dir(follow_symlinks=False):
                    plain_dirs.add(entry.name)
                if entry.is_dir():
                    dirs.append(entry.name)
                elif entry.is_file():
                    files.append(entry.name)
    except (FileNotFoundError, NotADirectoryError):
        return None
    return _DirScan(sorted(files), sorted(dirs), plain_dirs)


def _lstat_mode(path: str) -> int | None:
    """Return the mode of a path, without following symbolic links, or `None` if it is missing."""
    try:
        return os.lstat(path).st_mode
    except (FileNotFoundError, NotADirectoryError):
        return None


def _is_under(path: Path, directory: Path) -> bool:
    """Return whether a normalized path lies below a normalized directory."""
    if path == directory:
        return False
    if directory == ".":
        return not (path.isabs() or path == ".." or path.startswith(".." + os.sep))
    return path.startswith(directory.rstrip(os.sep) + os.sep)


def _parent_dir(path: Path) -> Path:
    """Return the parent directory of a normalized path."""
    parent = path.parent
    return Path(".") if parent == "" else parent
