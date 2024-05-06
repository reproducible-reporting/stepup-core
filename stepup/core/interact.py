# StepUp Core provides the basic framework for the StepUp build tool.
# Copyright (C) 2024 Toon Verstraelen
#
# This file is part of StepUp Core.
#
# StepUp Core is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 3
# of the License, or (at your option) any later version.
#
# StepUp Core is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, see <http://www.gnu.org/licenses/>
#
# --
"""Application Programming Interface (API) for interactive use of the director process.

Most of these functions are used for writing tests.
They can also be employed to create keyboard shortcuts within your IDE.

For example, one may bind the following command to an IDE's keyboard shortcut:

```bash
python -c 'from stepup.core.interact import run; run()'
```

This command must be executed in the top-level directory
where a `stepup` command is running in interactive mode.
"""

from .api import RPC_CLIENT, translate

__all__ = ("run", "cleanup", "graph", "watch_update", "watch_delete", "wait", "join")


def run():
    """Exit the watch phase and start running steps whose inputs have changed."""
    RPC_CLIENT.call.run()


def cleanup(*paths: str) -> tuple[int, int]:
    """Remove paths (if they are outputs), recursively removing all consumer files and directories.

    Parameters
    ----------
    paths
        A list of paths to consider for removal.
        Variable substitutions are not supported.

    Returns
    -------
    numf
        The number of files effectively removed.
    numd
        The number of directories effectively removed.
    """
    # Translate paths to directory working dir and make RPC call
    tr_paths = sorted(translate(path) for path in paths)
    return RPC_CLIENT.call.cleanup(tr_paths)


def graph(prefix: str):
    """Write the workflow graph files in text and dot formats."""
    return RPC_CLIENT.call.graph(prefix)


def watch_update(path: str):
    """Block until the watcher has observed an update of the file."""
    RPC_CLIENT.call.watch_update(path, _rpc_timeout=None)


def watch_delete(path: str):
    """Block until the watcher has observed the deletion of the file."""
    RPC_CLIENT.call.watch_delete(path, _rpc_timeout=None)


def wait():
    """Block until the runner has become idle."""
    RPC_CLIENT.call.wait(_rpc_timeout=None)


def join():
    """Wait for the runner to become idle and stop the director.

    This is the same as `wait()` followed by `shutdown()`."""
    RPC_CLIENT.call.join(_rpc_timeout=None)
