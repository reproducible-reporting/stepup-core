# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Behavioral tests shared by the wrappers around the file watching libraries."""

import asyncio
import os
import shutil
import sys

import pytest
from path import Path

from stepup.core import watcher
from stepup.core.enums import Change
from stepup.core.pytest import sort_change_runs
from stepup.core.watcher import AsyncInotifyWrapper, WatchfilesWrapper, select_watcher_backend

SETTLE_TIME = 0.2
"""How long to wait for changes that should not be reported, in seconds."""


@pytest.fixture(
    params=[
        pytest.param(
            AsyncInotifyWrapper,
            id="asyncinotify",
            marks=pytest.mark.skipif(sys.platform != "linux", reason="Linux only"),
        ),
        pytest.param(WatchfilesWrapper, id="watchfiles"),
    ]
)
async def wrapper(request, tmp_path, monkeypatch):
    """Provide a running wrapper that watches directories relative to an empty directory."""
    monkeypatch.chdir(tmp_path)
    if request.param is WatchfilesWrapper:
        rust_notify = pytest.importorskip("watchfiles._rust_notify").RustNotify
        monkeypatch.setattr(watcher, "RustNotify", rust_notify)
    async with request.param(dir_queue=asyncio.Queue()) as result:
        yield result


async def watch(wrapper, *paths: str):
    """Ask the wrapper to watch directories, and wait until all existing ones are watched."""
    for path in paths:
        wrapper.dir_queue.put_nowait(Path(path))
    async with asyncio.timeout(5):
        while not (
            wrapper.dir_queue.empty()
            and all(value is not None for path, value in wrapper.watches.items() if path.is_dir())
        ):
            await asyncio.sleep(0.01)


async def check(wrapper, expected: set, optional: frozenset = frozenset()) -> list:
    """Collect the reported changes and compare them to the expected ones.

    Parameters
    ----------
    wrapper
        The wrapper to collect changes from.
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
            seen.append(await wrapper.change_queue.get())
    await asyncio.sleep(SETTLE_TIME)
    while not wrapper.change_queue.empty():
        seen.append(wrapper.change_queue.get_nowait())
    assert expected <= set(seen) <= expected | optional, seen
    return seen


def write(path: str, content: str = "x"):
    with open(path, "w") as fh:
        fh.write(content)


async def test_file_changes(wrapper):
    await watch(wrapper, ".")
    write("a.txt")
    await check(wrapper, {(Change.UPDATED, "a.txt")})
    write("a.txt", "changed")
    await check(wrapper, {(Change.UPDATED, "a.txt")})
    os.remove("a.txt")
    await check(wrapper, {(Change.DELETED, "a.txt")})


async def test_flush(wrapper):
    if isinstance(wrapper, AsyncInotifyWrapper):
        pytest.skip("Flushing is a no-op for asyncinotify.")
    await watch(wrapper, "sub")
    write("a.txt")
    await wrapper.flush()
    assert wrapper.change_queue.get_nowait() == (Change.UPDATED, "a.txt")
    # A new directory is only watched after its files have been reported.
    os.mkdir("sub")
    write("sub/b.txt")
    await wrapper.flush()
    assert wrapper.watches[Path("sub")] is not None
    assert (Change.UPDATED, "sub/b.txt") in [
        wrapper.change_queue.get_nowait() for _ in range(wrapper.change_queue.qsize())
    ]


async def test_replace_file(wrapper):
    write("a.txt")
    await watch(wrapper, ".")
    os.remove("a.txt")
    write("a.txt", "new")
    seen = await check(wrapper, {(Change.DELETED, "a.txt"), (Change.UPDATED, "a.txt")})
    assert seen[0] == (Change.DELETED, "a.txt")
    assert seen[-1] == (Change.UPDATED, "a.txt")


async def test_missing_dir_appears(wrapper):
    await watch(wrapper, "sub/deeper")
    assert wrapper.watches[Path("sub/deeper")] is None
    # The file is written before the new directories can be watched.
    os.makedirs("sub/deeper")
    write("sub/deeper/x.txt")
    await check(wrapper, {(Change.UPDATED, "sub/deeper/x.txt")})
    await watch(wrapper)
    write("sub/deeper/y.txt")
    await check(wrapper, {(Change.UPDATED, "sub/deeper/y.txt")})


async def test_dir_removed_and_recreated(wrapper):
    os.mkdir("sub")
    write("sub/a.txt")
    await watch(wrapper, "sub")
    shutil.rmtree("sub")
    # asyncinotify reports the removed file and directory as deleted paths,
    # because inotify drops the watch of the directory before its parent reports the removal.
    # watchfiles reports the removed directory, and the removed file only if it came earlier.
    seen = await check(
        wrapper,
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
    assert wrapper.watches[Path("sub")] is None
    os.mkdir("sub")
    write("sub/b.txt")
    await check(wrapper, {(Change.UPDATED, "sub/b.txt")})


async def test_dir_moved_away_and_back(wrapper):
    os.mkdir("data")
    write("data/a.txt")
    await watch(wrapper, "data")
    os.rename("data", "other")
    await check(wrapper, {(Change.DELETED_PARENT, "data")}, frozenset([(Change.DELETED, "data")]))
    # Changes in the moved directory are not reported under its old path.
    write("other/b.txt")
    write("marker.txt")
    await check(wrapper, {(Change.UPDATED, "marker.txt")})
    os.rename("other", "data")
    await check(wrapper, {(Change.UPDATED, "data/a.txt"), (Change.UPDATED, "data/b.txt")})


async def test_unwatched_subdir(wrapper):
    os.mkdir("old")
    write("old/a.txt")
    await watch(wrapper, ".")
    os.mkdir("new")
    write("marker1.txt")
    await check(wrapper, {(Change.UPDATED, "marker1.txt")})
    # The removal of a directory is not mistaken for that of a file.
    shutil.rmtree("old")
    os.rmdir("new")
    write("marker2.txt")
    await check(wrapper, {(Change.UPDATED, "marker2.txt")})


async def test_no_changes_lost_while_adding_watches(wrapper):
    await watch(wrapper, ".")
    expected = set()
    for i in range(20):
        os.mkdir(f"d{i}")
        wrapper.dir_queue.put_nowait(Path(f"d{i}"))
        write(f"f{i}.txt")
        expected.add((Change.UPDATED, f"f{i}.txt"))
        await asyncio.sleep(0.02)
    await check(wrapper, expected)


def test_select_watcher_backend(monkeypatch):
    monkeypatch.delenv("STEPUP_WATCHER", raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    assert select_watcher_backend() == "asyncinotify"
    monkeypatch.setattr(sys, "platform", "darwin")
    assert select_watcher_backend() is None
    monkeypatch.setenv("STEPUP_WATCHER", "watchfiles")
    assert select_watcher_backend() == "watchfiles"
    monkeypatch.setenv("STEPUP_WATCHER", "asyncinotify")
    with pytest.raises(ValueError, match="only works on Linux"):
        select_watcher_backend()
    monkeypatch.setenv("STEPUP_WATCHER", "watchdog")
    with pytest.raises(ValueError, match="must be asyncinotify or watchfiles"):
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
