<!--
SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
SPDX-License-Identifier: LGPL-3.0-or-later
-->

# Watching the File System

This file documents `stepup/core/watch/`.
The layering of the subpackages and the process model are in `stepup/core/CLAUDE.md`.

## Watchers and Backends

`Watcher` (`director/watcher.py`) runs the watch phases and knows nothing about libraries.
A `WatcherBackend` (`backend.py`) turns the directories on its `dir_queue`
into watches, and file events into `(Change, path)` items on its `change_queue`.
`select_watcher_backend()` (`select.py`) picks one with `STEPUP_WATCHER`:

- `asyncinotify` (`inotify.py`): the default on Linux, and only available there.
- `watchfiles` (`watchfiles.py`): the default on macOS and Windows.
  It is a regular dependency outside Linux and an optional extra on Linux.

Rules that every backend must follow, and that `tests/watch/test_watcher_backends.py`
checks for all of them with one set of tests:

- Only changes of files are reported, apart from the removal of a watched directory.
- A directory that does not exist yet is recorded without a watch,
  and its nearest existing ancestor is watched until it appears.
  Nothing created in the gap may be missed.
- A change must not be reported when nothing changed.
  Windows reports a directory as modified lazily and without a cause that StepUp can use,
  so `_dir_changed` rescans only a directory that was reported as added.

Platform differences that shaped `WatchfilesBackend`,
as observed with watchfiles 1.3.0 (Windows 11 24H2 and macOS 26):

- watchfiles cannot add a path to a running watcher.
  A new *generation* of `RustNotify` instances therefore replaces the old one
  whenever directories are added, and takes over the instances that still fit.
  One thread owns the instances and translates their batches.
- On Windows, a watch keeps its directory open, which blocks the rename of every parent of it.
  `STEPUP_WATCHER_RECURSIVE` (default `1` on Windows, `0` elsewhere)
  replaces the watches of all directories below the working directory by one recursive watch,
  which leaves only the parents of the working directory locked.
  The changes in directories that StepUp has no interest in are dropped,
  so the reported changes are the same in both modes.
  The documented limitations for users are in "How Watch Mode Observes Changes"
  in `docs/reference/interactive.md`.
- Windows does not report the removal, rename or creation of a watched directory itself,
  but only a watch on its parent does.
  Linux does report the removal.
- Windows compares paths without regard to case, StepUp does not,
  so a file declared with another case than on disk gets no reports.
