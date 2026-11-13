# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Behavioral tests shared by the backends around the file watching libraries."""

import asyncio
import os
import shutil
import sys

import pytest

from stepup.core import watcher, watcher_watchfiles
from stepup.core.enums import Change
from stepup.core.exceptions import ConfigError
from stepup.core.path import Path
from stepup.core.pytest import sort_change_runs
from stepup.core.watcher import _import_backend_class, select_watcher_backend
from stepup.core.watcher_watchfiles import (
    WatchfilesBackend,
    _divide_dirs,
    select_recursive_watch,
)

SETTLE_TIME = 0.2
"""How long to wait for changes that should not be reported, in seconds."""


@pytest.fixture(
    params=[
        pytest.param(
            "asyncinotify",
            marks=pytest.mark.skipif(sys.platform != "linux", reason="Linux only"),
        ),
        "watchfiles",
        "watchfiles-recursive",
    ]
)
async def backend(request, workdir):
    """Provide a running backend, of every kind, that watches directories relative to `workdir`."""
    library, _, mode = request.param.partition("-")
    pytest.importorskip(library)
    backend_class = _import_backend_class(library)
    kwargs = {"recursive": mode == "recursive"} if library == "watchfiles" else {}
    async with backend_class(dir_queue=asyncio.Queue(), **kwargs) as result:
        yield result


@pytest.fixture
def workdir(tmp_path, monkeypatch) -> Path:
    """Make an empty directory the working directory, with room for directories next to it."""
    result = Path(tmp_path) / "work"
    result.mkdir()
    monkeypatch.chdir(result)
    return result


def locks_parents(backend) -> bool:
    """Return whether the watches of the backend keep Windows from renaming parent directories."""
    return sys.platform == "win32" and not backend.recursive


async def watch(backend, *paths: str):
    """Ask the backend to watch directories, and wait until all existing ones are watched."""
    requested = [Path(path) for path in paths]
    for path in requested:
        backend.dir_queue.put_nowait(path)
    async with asyncio.timeout(5):
        # An empty queue does not show that the backend has handled the last request,
        # because it takes a request from the queue before it records the directory.
        while not (
            backend.dir_queue.empty()
            and all(path in backend.watches for path in requested)
            and all(value is not None for path, value in backend.watches.items() if path.is_dir())
        ):
            await asyncio.sleep(0.01)


async def check(backend, expected: set, optional: frozenset = frozenset()) -> list:
    """Collect the reported changes and compare them to the expected ones.

    Parameters
    ----------
    backend
        The backend to collect changes from.
    expected
        The changes that must be reported, in any order and possibly more than once.
    optional
        Changes that may be reported, on top of the expected ones.

    Returns
    -------
    seen
        All changes reported, in the order they were received.
    """
    seen = []
    async with asyncio.timeout(5):
        while not expected <= set(seen):
            seen.append(await backend.change_queue.get())
    await asyncio.sleep(SETTLE_TIME)
    while not backend.change_queue.empty():
        seen.append(backend.change_queue.get_nowait())
    assert expected <= set(seen) <= expected | optional, seen
    return seen


async def settle(backend, *existing: str):
    """Wait until the backend has reported what happened before its watches were started.

    FSEvents (macOS) may report the creation of files shortly before a watch was started,
    see `WatchfilesBackend`.

    Parameters
    ----------
    backend
        The backend whose watches have just been started.
    existing
        The files created before the watches, which may be reported once more.
    """
    write("settle.txt")
    await check(
        backend,
        {(Change.UPDATED, "settle.txt")},
        frozenset((Change.UPDATED, path) for path in existing),
    )


def write(path: str, content: str = "x"):
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(content)


async def test_file_changes(backend):
    await watch(backend, ".")
    write("a.txt")
    await check(backend, {(Change.UPDATED, "a.txt")})
    write("a.txt", "changed")
    await check(backend, {(Change.UPDATED, "a.txt")})
    os.remove("a.txt")
    await check(backend, {(Change.DELETED, "a.txt")})


async def test_flush(backend):
    if not isinstance(backend, WatchfilesBackend):
        pytest.skip("Flushing is a no-op for asyncinotify.")
    await watch(backend, "sub")
    write("a.txt")
    await backend.flush()
    assert backend.change_queue.get_nowait() == (Change.UPDATED, "a.txt")
    # A new directory is only watched after its files have been reported.
    os.mkdir("sub")
    write("sub/b.txt")
    await backend.flush()
    assert backend.watches[Path("sub")] is not None
    assert (Change.UPDATED, "sub/b.txt") in [
        backend.change_queue.get_nowait() for _ in range(backend.change_queue.qsize())
    ]


async def test_replace_file(backend):
    write("a.txt")
    await watch(backend, ".")
    os.remove("a.txt")
    write("a.txt", "new")
    seen = await check(backend, {(Change.DELETED, "a.txt"), (Change.UPDATED, "a.txt")})
    # FSEvents (macOS) may also report the creation of the file before the watch started.
    assert seen[-1] == (Change.UPDATED, "a.txt")


async def test_missing_dir_appears(backend):
    await watch(backend, "sub/deeper")
    assert backend.watches[Path("sub/deeper")] is None
    # The file is written before the new directories can be watched.
    os.makedirs("sub/deeper")
    write("sub/deeper/x.txt")
    await check(backend, {(Change.UPDATED, "sub/deeper/x.txt")})
    await watch(backend)
    write("sub/deeper/y.txt")
    await check(backend, {(Change.UPDATED, "sub/deeper/y.txt")})


async def test_dir_removed_and_recreated(backend):
    os.mkdir("sub")
    write("sub/a.txt")
    await watch(backend, "sub")
    await settle(backend, "sub/a.txt")
    shutil.rmtree("sub")
    # The marker shows that the removal has been reported,
    # because changes are reported in the order in which they happened.
    write("marker.txt")
    # asyncinotify reports the removed file and directory as deleted paths,
    # because inotify drops the watch of the directory before its parent reports the removal.
    # watchfiles reports the removed directory, and the removed file only if it came earlier.
    seen = await check(
        backend,
        {(Change.UPDATED, "marker.txt")},
        frozenset(
            [
                (Change.DELETED, "sub/a.txt"),
                (Change.DELETED, "sub"),
                (Change.DELETED_PARENT, "sub"),
            ]
        ),
    )
    assert (Change.DELETED, "sub/a.txt") in seen or (Change.DELETED_PARENT, "sub") in seen
    assert backend.watches[Path("sub")] is None
    os.mkdir("sub")
    write("sub/b.txt")
    await check(backend, {(Change.UPDATED, "sub/b.txt")})


async def test_dir_moved_away_and_back(backend):
    os.mkdir("data")
    write("data/a.txt")
    await watch(backend, "data")
    await settle(backend, "data/a.txt")
    os.rename("data", "other")
    await check(backend, {(Change.DELETED_PARENT, "data")}, frozenset([(Change.DELETED, "data")]))
    # Changes in the moved directory are not reported under its old path.
    write("other/b.txt")
    write("marker.txt")
    await check(backend, {(Change.UPDATED, "marker.txt")})
    os.rename("other", "data")
    await check(backend, {(Change.UPDATED, "data/a.txt"), (Change.UPDATED, "data/b.txt")})


async def test_unwatched_subdir(backend):
    os.mkdir("old")
    write("old/a.txt")
    await watch(backend, ".")
    os.mkdir("new")
    write("marker1.txt")
    await check(backend, {(Change.UPDATED, "marker1.txt")})
    # The removal of a directory is not mistaken for that of a file.
    shutil.rmtree("old")
    os.rmdir("new")
    write("marker2.txt")
    await check(backend, {(Change.UPDATED, "marker2.txt")})


async def test_no_changes_lost_while_adding_watches(backend):
    await watch(backend, ".")
    expected = set()
    for i in range(20):
        os.mkdir(f"d{i}")
        backend.dir_queue.put_nowait(Path(f"d{i}"))
        write(f"f{i}.txt")
        expected.add((Change.UPDATED, f"f{i}.txt"))
        await asyncio.sleep(0.02)
    await check(backend, expected)


async def test_only_changed_file_reported(backend):
    os.mkdir("sub")
    write("sub/a.txt")
    write("sub/b.txt")
    await watch(backend, "sub")
    await settle(backend, "sub/a.txt", "sub/b.txt")
    write("sub/a.txt", "changed")
    await check(backend, {(Change.UPDATED, "sub/a.txt")})
    write("sub/c.txt")
    await check(backend, {(Change.UPDATED, "sub/c.txt")})
    os.remove("sub/b.txt")
    await check(backend, {(Change.DELETED, "sub/b.txt")})


async def test_modified_dir(backend):
    if not isinstance(backend, WatchfilesBackend):
        pytest.skip("The asyncinotify backend reports the files of a directory that has changed.")
    os.mkdir("sub")
    write("sub/a.txt")
    await watch(backend, "sub")
    await settle(backend, "sub/a.txt")
    # Windows also reports a directory as modified when its entries change, at a time of its own.
    os.utime("sub")
    write("marker.txt")
    await check(backend, {(Change.UPDATED, "marker.txt")})


async def test_dir_replaced_by_rename(backend):
    if sys.platform == "win32":
        pytest.skip("Windows does not rename a directory over another one.")
    os.mkdir("sub")
    os.mkdir("new")
    write("new/a.txt")
    await watch(backend, "sub")
    await settle(backend)
    os.rename("new", "sub")
    await check(
        backend,
        {(Change.UPDATED, "sub/a.txt")},
        frozenset([(Change.DELETED_PARENT, "sub"), (Change.DELETED, "sub")]),
    )
    write("sub/b.txt")
    await check(backend, {(Change.UPDATED, "sub/b.txt")})


async def test_contents_of_unwatched_subdir(backend):
    os.makedirs("other/deep")
    await watch(backend, ".")
    write("other/a.txt")
    write("other/deep/b.txt")
    os.mkdir("other/new")
    write("other/new/c.txt")
    os.remove("other/a.txt")
    write("marker.txt")
    await check(backend, {(Change.UPDATED, "marker.txt")})


async def test_parent_of_watched_dir_renamed(backend):
    if locks_parents(backend):
        pytest.skip("Windows does not rename a directory with a watched directory in it.")
    os.makedirs("top/sub")
    write("top/sub/a.txt")
    await watch(backend, "top/sub")
    await settle(backend, "top/sub/a.txt")
    os.rename("top", "moved")
    await check(backend, {(Change.DELETED_PARENT, "top")}, frozenset([(Change.DELETED, "top")]))
    os.rename("moved", "top")
    await check(backend, {(Change.UPDATED, "top/sub/a.txt")})
    await watch(backend)
    write("top/sub/b.txt")
    await check(backend, {(Change.UPDATED, "top/sub/b.txt")})


async def test_dir_outside_working_dir(backend):
    os.makedirs("../outside/sub")
    write("../outside/sub/a.txt")
    await watch(backend, ".", "../outside/sub")
    write("../outside/sub/a.txt", "changed")
    write("b.txt")
    await check(backend, {(Change.UPDATED, "../outside/sub/a.txt"), (Change.UPDATED, "b.txt")})
    shutil.rmtree("../outside/sub")
    write("marker.txt")
    seen = await check(
        backend,
        {(Change.UPDATED, "marker.txt")},
        frozenset(
            [
                (Change.DELETED, "../outside/sub/a.txt"),
                (Change.DELETED, "../outside/sub"),
                (Change.DELETED_PARENT, "../outside/sub"),
            ]
        ),
    )
    assert (Change.DELETED, "../outside/sub/a.txt") in seen or (
        Change.DELETED_PARENT,
        "../outside/sub",
    ) in seen
    os.mkdir("../outside/sub")
    write("../outside/sub/c.txt")
    await check(backend, {(Change.UPDATED, "../outside/sub/c.txt")})
    write("d.txt")
    await check(backend, {(Change.UPDATED, "d.txt")})


def symlink_dir(target: str, link: str):
    """Create a symbolic link to a directory, or skip the test when that is not allowed."""
    try:
        # Windows does not resolve a link whose target is written with forward slashes.
        os.symlink(os.path.normpath(target), link, target_is_directory=True)
    except OSError:
        pytest.skip("Symbolic links cannot be created.")


async def test_symlink_to_dir_outside_working_dir(backend):
    os.mkdir("../outside")
    symlink_dir("../outside", "link")
    await watch(backend, "link")
    write("link/a.txt")
    await check(backend, {(Change.UPDATED, "link/a.txt")})
    write("../outside/a.txt", "changed")
    await check(backend, {(Change.UPDATED, "link/a.txt")})


async def test_symlink_to_watched_dir(backend):
    if not isinstance(backend, WatchfilesBackend):
        pytest.skip("inotify has one watch per directory, whatever the number of paths to it.")
    os.mkdir("real")
    symlink_dir("real", "link")
    await watch(backend, "real", "link")
    write("real/a.txt")
    await check(backend, {(Change.UPDATED, "real/a.txt"), (Change.UPDATED, "link/a.txt")})


@pytest.fixture
def notify_calls(monkeypatch) -> list[dict]:
    """Record the arguments of every `RustNotify` instance that is created."""
    pytest.importorskip("watchfiles")
    rust_notify = watcher_watchfiles._import_rust_notify()
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        return rust_notify(**kwargs)

    monkeypatch.setattr(watcher_watchfiles, "_import_rust_notify", lambda: create)
    return calls


async def test_recursive_watch_is_kept(workdir, notify_calls):
    async with WatchfilesBackend(dir_queue=asyncio.Queue(), recursive=True) as backend:
        os.mkdir("old")
        await watch(backend, "old")
        os.makedirs("new/deeper")
        await watch(backend, "new/deeper")
        write("new/deeper/a.txt")
        await check(backend, {(Change.UPDATED, "new/deeper/a.txt")})
        shutil.rmtree("new")
        await check(
            backend,
            {(Change.DELETED_PARENT, "new")},
            frozenset(
                [(Change.DELETED, "new/deeper/a.txt"), (Change.DELETED_PARENT, "new/deeper")]
            ),
        )
        os.makedirs("new/deeper")
        write("new/deeper/b.txt")
        await check(backend, {(Change.UPDATED, "new/deeper/b.txt")})
        assert [(call["watch_paths"], call["recursive"]) for call in notify_calls] == [
            ([workdir.realpath()], True)
        ]
        # A directory elsewhere gets a watch of its own, next to the recursive one.
        os.mkdir("../outside")
        await watch(backend, "../outside")
        write("../outside/c.txt")
        write("old/d.txt")
        await check(backend, {(Change.UPDATED, "../outside/c.txt"), (Change.UPDATED, "old/d.txt")})
        assert [(call["watch_paths"], call["recursive"]) for call in notify_calls[1:]] == [
            ([(workdir.parent / "outside").realpath()], False)
        ]


async def test_separate_watches_are_replaced(workdir, notify_calls):
    async with WatchfilesBackend(dir_queue=asyncio.Queue(), recursive=False) as backend:
        await watch(backend, ".")
        os.mkdir("sub")
        await watch(backend, "sub")
        assert [(call["watch_paths"], call["recursive"]) for call in notify_calls] == [
            ([workdir.realpath()], False),
            ([workdir.realpath(), (workdir / "sub").realpath()], False),
        ]


@pytest.mark.parametrize(
    ("dirs", "root", "instances"),
    [
        ([], None, [(frozenset(), False)]),
        (["/w", "/w/a", "/x"], None, [(frozenset(["/w", "/w/a", "/x"]), False)]),
        ([], "/w", []),
        (["/w", "/w/a", "/w/a/b"], "/w", [(frozenset(["/w"]), True)]),
        (["/w/a"], "/w", [(frozenset(["/w"]), True)]),
        (
            ["/w", "/w/a", "/x", "/wa", "/"],
            "/w",
            [(frozenset(["/w"]), True), (frozenset(["/x", "/wa", "/"]), False)],
        ),
        (["/x", "/x/y"], "/w", [(frozenset(["/x", "/x/y"]), False)]),
        (["/", "/x", "/x/y"], "/", [(frozenset(["/"]), True)]),
        (
            ["C:/w/a", "C:/", "D:/w/a"],
            "C:/w",
            [
                (frozenset(["C:/w"]), True),
                (frozenset(["C:/", "D:/w/a"]), False),
            ],
        ),
    ],
)
def test_divide_dirs(dirs, root, instances):
    root = None if root is None else Path(root)
    assert _divide_dirs([Path(path) for path in dirs], root) == instances


@pytest.mark.parametrize(
    ("platform", "value", "recursive"),
    [
        ("linux", "", False),
        ("linux", "0", False),
        ("linux", "1", True),
        ("darwin", "", False),
        ("darwin", "1", True),
        ("win32", "", True),
        ("win32", "0", False),
        ("win32", "1", True),
    ],
)
def test_select_recursive_watch(monkeypatch, platform, value, recursive):
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setenv("STEPUP_WATCHER_RECURSIVE", value)
    assert select_recursive_watch() is recursive


def test_select_recursive_watch_unset(monkeypatch):
    monkeypatch.delenv("STEPUP_WATCHER_RECURSIVE", raising=False)
    assert select_recursive_watch() is (sys.platform == "win32")


def test_select_recursive_watch_rejects(monkeypatch):
    monkeypatch.setenv("STEPUP_WATCHER_RECURSIVE", "yes")
    with pytest.raises(ConfigError, match="STEPUP_WATCHER_RECURSIVE must be 0 or 1, got 'yes'"):
        select_recursive_watch()


@pytest.fixture
def installed_watchers(monkeypatch):
    """Pretend that both file watching libraries are installed, whatever the platform."""
    monkeypatch.setattr(watcher.importlib.util, "find_spec", lambda name: object())


@pytest.mark.parametrize(
    ("platform", "value", "backend"),
    [
        ("linux", "", "asyncinotify"),
        ("linux", "asyncinotify", "asyncinotify"),
        ("linux", "watchfiles", "watchfiles"),
        ("darwin", "", "watchfiles"),
        ("darwin", "watchfiles", "watchfiles"),
        ("win32", "", "watchfiles"),
    ],
)
@pytest.mark.usefixtures("installed_watchers")
def test_select_watcher_backend(monkeypatch, platform, value, backend):
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setenv("STEPUP_WATCHER", value)
    assert select_watcher_backend() == backend


@pytest.mark.parametrize(
    ("platform", "value", "message"),
    [
        ("linux", "watchdog", "must be asyncinotify or watchfiles on linux, got 'watchdog'"),
        ("darwin", "asyncinotify", "must be watchfiles on darwin, got 'asyncinotify'"),
    ],
)
@pytest.mark.usefixtures("installed_watchers")
def test_select_watcher_backend_rejects(monkeypatch, platform, value, message):
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setenv("STEPUP_WATCHER", value)
    with pytest.raises(ConfigError, match=message):
        select_watcher_backend()


@pytest.mark.usefixtures("installed_watchers")
def test_select_watcher_backend_checks_recursive(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("STEPUP_WATCHER_RECURSIVE", "yes")
    # The setting only matters for the library that uses it.
    monkeypatch.setenv("STEPUP_WATCHER", "asyncinotify")
    assert select_watcher_backend() == "asyncinotify"
    monkeypatch.setenv("STEPUP_WATCHER", "watchfiles")
    with pytest.raises(ConfigError, match="STEPUP_WATCHER_RECURSIVE must be 0 or 1"):
        select_watcher_backend()


def test_select_watcher_backend_not_installed(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("STEPUP_WATCHER", "watchfiles")
    monkeypatch.setattr(
        watcher.importlib.util, "find_spec", lambda name: None if name == "watchfiles" else object()
    )
    with pytest.raises(ConfigError, match="needs the watchfiles library, which is not installed"):
        select_watcher_backend()


def test_sort_change_runs():
    text = "\n".join(
        [
            "   PHASE │ watch",
            " UPDATED │ plan.py",
            " DELETED │ orig.txt",
            " DELETED │ plan.py",
            "   PHASE │ build",
            " UPDATED │ b.txt",
            " UPDATED │ a.txt",
        ]
    )
    assert sort_change_runs(text) == "\n".join(
        [
            "   PHASE │ watch",
            " DELETED │ orig.txt",
            " UPDATED │ plan.py",
            " DELETED │ plan.py",
            "   PHASE │ build",
            " UPDATED │ a.txt",
            " UPDATED │ b.txt",
        ]
    )
