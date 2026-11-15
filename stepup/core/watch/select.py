# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Select the file watching library and import the backend that uses it."""

import importlib.util
import logging
import os
import sys

from ..exceptions import ConfigError
from .backend import WatcherBackend
from .watchfiles import WatchfilesBackend, select_recursive_watch

__all__ = (
    "import_backend_class",
    "select_watcher_backend",
)

logger = logging.getLogger(__name__)


def select_watcher_backend() -> str:
    """Select the library that watches the file system in watch mode.

    On Linux, `asyncinotify` is used by default,
    and `STEPUP_WATCHER=watchfiles` selects the `watchfiles` library instead,
    which is an optional dependency on this platform.
    On other platforms, `watchfiles` is the only choice.
    How `watchfiles` is used is a separate setting, see `select_recursive_watch`,
    which is checked here too, so that this function validates all settings of the file watcher.

    Returns
    -------
    backend
        The name of the library, `asyncinotify` or `watchfiles`.

    Raises
    ------
    ConfigError
        If `STEPUP_WATCHER` names a library that cannot be used on this platform,
        if the selected library is not installed,
        or if `STEPUP_WATCHER_RECURSIVE` is invalid while `watchfiles` is selected.
    """
    choices = ("asyncinotify", "watchfiles") if sys.platform == "linux" else ("watchfiles",)
    backend = os.environ.get("STEPUP_WATCHER", "") or choices[0]
    if backend not in choices:
        raise ConfigError(
            f"STEPUP_WATCHER must be {' or '.join(choices)} on {sys.platform}, got {backend!r}."
        )
    if importlib.util.find_spec(backend) is None:
        raise ConfigError(f"Watch mode needs the {backend} library, which is not installed.")
    if backend == "watchfiles":
        select_recursive_watch()
    return backend


def import_backend_class(backend: str) -> type[WatcherBackend]:
    """Return the class of the backend that `select_watcher_backend` has selected.

    The module of the `asyncinotify` backend is only imported when it is selected,
    because the `asyncinotify` library is only installed on Linux.
    """
    if backend == "watchfiles":
        return WatchfilesBackend
    return importlib.import_module(".inotify", __package__).InotifyBackend
