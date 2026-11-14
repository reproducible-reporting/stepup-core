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
import sys
import threading
from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING

import attrs

from .asyncio import iter_until_stopped, wait_for_any_event
from .enums import Change
from .exceptions import ConfigError
from .path import Path
from .watcher_backend import WatcherBackend

if TYPE_CHECKING:
    from watchfiles._rust_notify import RustNotify

__all__ = ("WatchfilesBackend", "select_recursive_watch")


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


def select_recursive_watch() -> bool:
    """Decide how `WatchfilesBackend` watches the directories below the working directory.

    The default is one recursive watch on Windows and a watch per directory elsewhere,
    for the reasons given in `WatchfilesBackend.recursive`.
    `STEPUP_WATCHER_RECURSIVE=1` and `STEPUP_WATCHER_RECURSIVE=0` override the default.

    Returns
    -------
    recursive
        `True` for one recursive watch, `False` for a watch per directory.

    Raises
    ------
    ConfigError
        If `STEPUP_WATCHER_RECURSIVE` is set to something else than `0` or `1`.
    """
    value = os.environ.get("STEPUP_WATCHER_RECURSIVE", "")
    if value == "":
        return sys.platform == "win32"
    if value not in ("0", "1"):
        raise ConfigError(f"STEPUP_WATCHER_RECURSIVE must be 0 or 1, got {value!r}.")
    return value == "1"


@attrs.define
class WatchfilesBackend(WatcherBackend):
    """Interface between a `Watcher` instance and the `watchfiles` library.

    It behaves as described for `WatcherBackend`, except for three details.
    The first two are due to watchfiles handing over changes as an unordered set
    (as of watchfiles 1.3.0):

    - The changes observed within one batch (about 10 milliseconds)
      are reported in a fixed order instead of the order in which they happened.
    - When the changes of a path within one batch contradict each other,
      the current state of the path decides what is reported.
      For example, a file that was created and removed within one batch
      is only reported as removed.
    - On macOS, a file created shortly before its directory is watched may be reported as updated,
      because FSEvents also passes on changes from shortly before a watch was started
      (observed on macOS 26.7 with watchfiles 1.3.0).

    watchfiles cannot add paths to a running watcher (as of watchfiles 1.3.0).
    Each time new directories must be watched, a new generation is created to watch all of them.
    A generation consists of one or two `RustNotify` instances,
    and takes over the instances of its predecessor that already watch what it needs.
    One thread owns all instances, reads their changes and replaces them on request.
    """

    recursive: bool = attrs.field(kw_only=True, factory=select_recursive_watch)
    """Whether the directories below the working directory share one recursive watch.

    When `False`, every directory in `watches` gets a watch of its own.
    When `True`, a single watch on the working directory covers all directories below it,
    and only the directories elsewhere get a watch of their own.
    The changes in directories that are not in `watches` are dropped,
    so the same changes are reported either way.

    On Windows, a watch keeps its directory open,
    which makes it impossible to rename any parent of that directory
    (observed on Windows 11 24H2 with watchfiles 1.3.0).
    The recursive watch only has that effect on the parents of the working directory.
    On Linux, a recursive watch is the more expensive one,
    because inotify then needs a watch for every directory below the working directory,
    also for those StepUp has no interest in.
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

    _identities: dict[Path, tuple[int, int] | None] = attrs.field(init=False, factory=dict)
    """The identity of each watched directory when it was last listed, see `_dir_identity`."""

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
    """The path under which `RustNotify` reports the changes of each watched directory."""

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
                    generation, scans, missing = _create_generation(
                        *command, self.recursive, current
                    )
                    for notifier in () if current is None else current.notifiers:
                        if notifier in generation.notifiers:
                            continue
                        # An old instance is drained after the new ones have started,
                        # so every change is reported by at least one of them.
                        # A stop event would discard the changes that are not returned yet.
                        changes = notifier.notify.watch(
                            WATCHFILES_DEBOUNCE_MS, WATCHFILES_STEP_MS, WATCHFILES_STEP_MS, None
                        )
                        self._send_changes(loop, current, changes)
                        notifier.notify.close()
                    current = generation
                    self._send(
                        loop,
                        functools.partial(
                            self._apply_generation, generation.number, scans, missing
                        ),
                    )
                else:
                    # A call that starts after a flush request returns all changes made before it,
                    # unless notify takes longer than one step to pass a change on.
                    request = self._flush_requested
                    flushing = request > self._flush_sent
                    if len(current.notifiers) == 0 and not flushing:
                        # Nothing can be watched, so there is no call to wait in.
                        self._closing.wait(WATCHFILES_TIMEOUT_MS / 1000)
                    for notifier in current.notifiers:
                        changes = notifier.notify.watch(
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
            for notifier in () if current is None else current.notifiers:
                notifier.notify.close()

    def _send(self, loop: asyncio.AbstractEventLoop, work: Callable[[], None]):
        """Hand work over from the reader thread to `change_loop`."""
        loop.call_soon_threadsafe(self._work.put_nowait, work)

    def _send_changes(self, loop: asyncio.AbstractEventLoop, generation: "_Generation", changes):
        """Hand the result of `RustNotify.watch` over to `change_loop`, if it has any changes.

        A result other than a set means that the call returned without changes.
        The changes are translated here, in the reader thread,
        so that the ones outside the watched directories never reach the event loop.
        A recursive watch reports many of those,
        e.g. every write to the workflow database during a build.
        """
        if isinstance(changes, set):
            codes, sources = generation.translate(changes)
            if len(codes) > 0:
                self._send(
                    loop,
                    functools.partial(self._process_changes, generation.number, codes, sources),
                )

    def _apply_generation(
        self, number: int, scans: dict[Path, tuple[str, "_DirScan"]], missing: list[Path]
    ):
        """Record the directories that a new generation watches.

        Parameters
        ----------
        number
            The number of the new generation.
        scans, missing
            See `_create_generation`.
        """
        installing = self._installing
        self._installing = {}
        for path in missing:
            if self.watches.get(path) is not None:
                self._lose_dir(path)
            if path.is_dir():
                # It has been created again in the meantime.
                self._schedule_tree(path)
        for path, (real_path, _) in scans.items():
            self.watches[path] = number
            self._real_paths[path] = real_path
        for path, (_, scan) in scans.items():
            self._apply_scan(path, scan, report_files=installing[path])
        self._installed.set()

    def _process_changes(
        self, number: int, codes: dict[Path, set[int]], sources: dict[Path, set[Path]]
    ):
        """Turn a batch of changes into items for the change_queue.

        The watched directories in the batch are handled first,
        so that the removal of one of them stops the reporting of changes inside it.
        Within each group, the paths are handled in sorted order.
        Changes that came through a watch that has been dropped since are stale and ignored.

        Parameters
        ----------
        number
            The number of the generation that reported the changes.
        codes, sources
            See `_Generation.translate`.
        """
        for path in sorted(codes, key=lambda path: (path not in self.watches, path)):
            if not any(self._watched_in(source, number) for source in sources[path]):
                continue
            if path in self.watches:
                self._dir_changed(path, codes[path], number)
            else:
                self._entry_changed(path, codes[path])

    def _watched_in(self, directory: Path, number: int) -> bool:
        """Return whether the current watch of a directory is part of the given generation."""
        first = self.watches.get(directory)
        return first is not None and first <= number

    def _dir_changed(self, path: Path, codes: set[int], number: int):
        """Handle the changes of a directory that must be watched.

        A modified directory is left alone, because its own watch reports what changed in it.
        Windows reports a directory as modified whenever one of its entries changes
        (observed on Windows 11 24H2 with watchfiles 1.3.0).
        It does so with a delay, e.g. when the directory is listed for the first time afterwards,
        which may be when the watch of that directory has been requested but is not installed yet.
        Such a directory has not appeared, so its files are not reported.

        A directory reported as created is only listed again when it has been replaced,
        because FSEvents (macOS) also reports a directory as created
        when that happened shortly before the watch was started,
        or when it changes some time after its creation, e.g. in modification time
        (observed on macOS 26.7 with watchfiles 1.3.0).
        """
        if _DELETED in codes and self._watched_in(path, number):
            self._lose_dir(path)
        if path.is_dir():
            if self.watches[path] is None:
                if _ADDED in codes or not self._is_scheduled(path):
                    self._schedule_tree(path)
            elif _ADDED in codes and self._is_replaced(path):
                self._rescan(path)

    def _is_replaced(self, path: Path) -> bool:
        """Return whether a watched directory is another one than when it was last listed.

        A directory whose identity is unknown, before or now, counts as replaced.
        """
        identity = self._identities.get(path)
        return identity is None or identity != _dir_identity(path)

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
                self._identities.pop(other, None)
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
        self._identities[path] = scan.identity
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

        This happens when a watched directory turns out to be a new one,
        because its watch belongs to the directory it replaced.
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


@attrs.define(eq=False)
class _Notifier:
    """A `RustNotify` instance, together with what it watches."""

    notify: "RustNotify" = attrs.field()
    """The instance itself."""

    paths: frozenset[str] = attrs.field()
    """The directories given to the instance."""

    recursive: bool = attrs.field()
    """Whether the instance also reports the changes in the directories below `paths`."""


@attrs.define
class _Generation:
    """The `RustNotify` instances in use, together with the directories watched through them."""

    number: int = attrs.field()
    """Sequence number, which tells the changes of a newer generation from those of an older one."""

    notifiers: list[_Notifier] = attrs.field()
    """The instances, some of which may also have been part of the previous generation."""

    dirs: dict[str, list[Path]] = attrs.field()
    """The watched directories, keyed by the path under which `RustNotify` reports them.

    Symbolic links can lead to the same directory from more than one path,
    so each value is a list of directories as `WatchfilesBackend` knows them.
    """

    def translate(
        self, changes: set[tuple[int, str]]
    ) -> tuple[dict[Path, set[int]], dict[Path, set[Path]]]:
        """Express a batch of changes from `RustNotify.watch` in the paths of `WatchfilesBackend`.

        Only the changes of watched directories and of their entries are kept.

        Returns
        -------
        codes
            The codes of the changes of each path.
        sources
            The watched directories through which the changes of each path were seen.
        """
        codes: dict[Path, set[int]] = {}
        sources: dict[Path, set[Path]] = {}
        for code, reported in changes:
            # The constructor converts the separators that Windows reports.
            real_path = Path(reported)
            parent, name = real_path.splitpath()
            # A change inside a watched directory, or of a watched directory itself.
            pairs = [
                (directory, (directory / name).normpath())
                for directory in self.dirs.get(parent, ())
            ]
            pairs.extend((directory, directory) for directory in self.dirs.get(real_path, ()))
            for directory, path in pairs:
                codes.setdefault(path, set()).add(code)
                sources.setdefault(path, set()).add(directory)
        return codes, sources


@attrs.define(frozen=True)
class _DirScan:
    """The entries of a directory, each list sorted by name."""

    files: list[str] = attrs.field()
    """The names of the files, including symbolic links to files."""

    dirs: list[str] = attrs.field()
    """The names of the directories, including symbolic links to directories."""

    plain_dirs: set[str] = attrs.field()
    """The names of the directories that are not symbolic links."""

    identity: tuple[int, int] | None = attrs.field()
    """The identity of the directory itself, see `_dir_identity`."""


def _create_generation(
    number: int,
    keep: list[tuple[Path, str]],
    new: list[Path],
    recursive: bool,
    previous: _Generation | None,
) -> tuple[_Generation, dict[Path, tuple[str, _DirScan]], list[Path]]:
    """Create a generation that watches the given directories.

    Parameters
    ----------
    number
        The number of the new generation.
    keep
        The directories that are watched already,
        with the paths under which `RustNotify` reports them.
    new
        The directories to watch from now on.
    recursive
        Whether the directories below the working directory share one recursive watch,
        see `WatchfilesBackend.recursive`.
    previous
        The generation to replace.
        Its instances that already watch what the new generation needs are taken over,
        and the other ones are left for the caller to close.

    Returns
    -------
    generation
        The new generation.
    scans
        The entries of the new directories, listed after their watches were installed,
        each with the path under which `RustNotify` reports the directory.
    missing
        The directories that could not be watched, because they no longer exist.
    """
    # The paths are resolved, because that is how the changes below a recursive watch,
    # which does not follow symbolic links, are matched with the directories they belong to.
    real_paths = dict(keep)
    real_paths.update((path, path.realpath()) for path in new if path.is_dir())
    root = Path.cwd().realpath() if recursive else None
    old_notifiers = [] if previous is None else previous.notifiers
    attempt = 1
    while True:
        dirs: dict[str, list[Path]] = {}
        for path, real_path in real_paths.items():
            dirs.setdefault(real_path, []).append(path)
        # A new directory needs a new watch, also when its path was watched before,
        # because the old watch belongs to a directory that has been removed since.
        fresh = {real_paths[path] for path in new if path in real_paths}
        notifiers = []
        try:
            for paths, nested in _divide_dirs(dirs, root):
                for notifier in old_notifiers:
                    if (
                        notifier.paths == paths
                        and notifier.recursive == nested
                        and paths.isdisjoint(fresh)
                    ):
                        break
                else:
                    notify = _import_rust_notify()(
                        watch_paths=sorted(paths),
                        debug=False,
                        force_polling=False,
                        poll_delay_ms=0,
                        recursive=nested,
                        ignore_permission_denied=False,
                    )
                    notifier = _Notifier(notify, paths, nested)
                notifiers.append(notifier)
            break
        except FileNotFoundError:
            # A directory has been removed in the meantime,
            # which the generation that is still running reports.
            for notifier in notifiers:
                if notifier not in old_notifiers:
                    notifier.notify.close()
            if attempt == WATCHFILES_MAX_ATTEMPTS:
                raise
            attempt += 1
            real_paths = {
                path: real_path
                for path, real_path in real_paths.items()
                if Path(real_path).is_dir()
            }
    scans = {}
    for path in new:
        if path in real_paths:
            scan = _scan_dir(path)
            if scan is not None:
                scans[path] = (real_paths[path], scan)
    missing = [path for path, _ in keep if path not in real_paths]
    missing.extend(path for path in new if path not in scans)
    return _Generation(number, notifiers, dirs), scans, missing


def _divide_dirs(dirs: Iterable[str], root: str | None) -> list[tuple[frozenset[str], bool]]:
    """Divide the directories to watch over `RustNotify` instances.

    Parameters
    ----------
    dirs
        The directories to watch, with symbolic links resolved.
    root
        The directory to watch recursively when one of `dirs` is at or below it,
        or `None` to watch every directory separately.

    Returns
    -------
    instances
        For each instance, the directories to give it and whether it watches them recursively.
        An instance without directories is left out.
    """
    flat = frozenset(dirs)
    instances = []
    if root is not None:
        covered = frozenset(path for path in flat if path == root or _is_under(path, root))
        if len(covered) > 0:
            instances.append((frozenset([root]), True))
            flat -= covered
    if len(flat) > 0 or root is None:
        instances.append((flat, False))
    return instances


def _scan_dir(path: Path) -> _DirScan | None:
    """List the entries of a directory, or return `None` when it no longer exists."""
    files = []
    dirs = []
    plain_dirs = set()
    # When the directory is replaced during the scan,
    # the old identity makes the next change of the directory list it again.
    identity = _dir_identity(path)
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
    return _DirScan(sorted(files), sorted(dirs), plain_dirs, identity)


def _dir_identity(path: Path) -> tuple[int, int] | None:
    """Return the device and inode numbers of a directory, following symbolic links.

    Returns
    -------
    identity
        The numbers, or `None` when the directory is missing
        or when the file system has no inode numbers.
    """
    try:
        info = os.stat(path)
    except (FileNotFoundError, NotADirectoryError):
        return None
    if info.st_ino == 0:
        return None
    return info.st_dev, info.st_ino


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
        return not (path.isabs() or path == ".." or path.startswith("../"))
    return path.startswith(directory.rstrip("/") + "/")


def _parent_dir(path: Path) -> Path:
    """Return the parent directory of a normalized path."""
    parent = path.parent
    return Path(".") if parent == "" else parent
