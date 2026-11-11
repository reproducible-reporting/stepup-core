# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Behavioral tests shared by the backends around the file watching libraries."""

import asyncio
import os
import shutil
import sys

import pytest
from path import Path

from stepup.core import watcher
from stepup.core.enums import Change
from stepup.core.exceptions import ConfigError
from stepup.core.pytest import sort_change_runs
from stepup.core.watcher import _import_backend_class, select_watcher_backend
from stepup.core.watcher_watchfiles import WatchfilesBackend

SETTLE_TIME = 0.2
"""How long to wait for changes that should not be reported, in seconds."""


@pytest.fixture(
    params=[
        pytest.param(
            "asyncinotify",
            marks=pytest.mark.skipif(sys.platform != "linux", reason="Linux only"),
        ),
        "watchfiles",
    ]
)
async def backend(request, tmp_path, monkeypatch):
    """Provide a running backend that watches directories relative to an empty directory."""
    monkeypatch.chdir(tmp_path)
    pytest.importorskip(request.param)
    backend_class = _import_backend_class(request.param)
    async with backend_class(dir_queue=asyncio.Queue()) as result:
        yield result


async def watch(backend, *paths: str):
    """Ask the backend to watch directories, and wait until all existing ones are watched."""
    for path in paths:
        backend.dir_queue.put_nowait(Path(path))
    async with asyncio.timeout(5):
        while not (
            backend.dir_queue.empty()
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
    shutil.rmtree("sub")
    # asyncinotify reports the removed file and directory as deleted paths,
    # because inotify drops the watch of the directory before its parent reports the removal.
    # watchfiles reports the removed directory, and the removed file only if it came earlier.
    seen = await check(
        backend,
        set(),
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
