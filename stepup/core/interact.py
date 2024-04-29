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
"""Application programming interface to the director to simulate interactive use.

Most of these functions are used for writing tests.
They can also be used to create keyboard shortcuts in your IDE to control StepUp.
"""


from .api import RPC_CLIENT


__all__ = ("run", "graph", "watch_update", "watch_delete", "wait", "join")


def run():
    """Exit the watch phase and start running steps whose inputs have changed."""
    RPC_CLIENT.call.run()


def graph(path: str):
    """Write the workflow graph in text format to a file."""
    return RPC_CLIENT.call.graph(path)


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
