# StepUp Core provides the basic framework for the StepUp build tool.
# © 2024–2025 Toon Verstraelen
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
STEPUP_DIRECTOR_SOCKET=$(python -c "import stepup.core.director; \
print(stepup.core.director.get_socket())") \
python -c 'from stepup.core.interact import run; run()'
```

This command must be executed in the top-level directory
where a `stepup` command is running in interactive mode.

You can better understand how the above example works by breaking it down into two parts:

- The command `python -c "import stepup.core.director; print(stepup.core.director.get_socket())"`
  prints the path to the socket where the director listens for instructions.
  This is a randomized temporary path that is created when `stepup` is started.
  (For technical reasons, this path cannot be deterministic
  and must be read from `.stepup/log/director`.)
  By wrapping this command in `STEPUP_DIRECTOR_SOCKET=$(...)`, the path will be
  assigned to an environment variable `STEPUP_DIRECTOR_SOCKET`,
  which will be available for the second Python call.
- The part `python -c 'from stepup.core.interact import run; run()'`
  has the same effect as pressing `r` in the terminal where StepUp is running.
  The variable `STEPUP_DIRECTOR_SOCKET` tells which instance of StepUp to interact with.
  When StepUp runs `plan.py` scripts, they also use this environment variable
  to interact with the director process.
  As these are subprocesses of the director process, the variable is set automatically,
  so you don't need to set it.
  This is only needed if processes other than subprocesses need to interact with the director,
  as in this example.

## Configuration of a Task in VSCode

You can define a
[Custom Task in VSCode](https://code.visualstudio.com/docs/editor/tasks#_custom-tasks)
to rerun StepUp with the following `tasks.json` file:

```json
{
  "version": "2.0.0",
  "tasks": [
    {
      "label": "StepUp run",
      "type": "shell",
      "command": "STEPUP_DIRECTOR_SOCKET=$(python -c 'import stepup.core.director; \
print(stepup.core.director.get_socket())') python -c 'from stepup.core.interact import run; run()'",
      "options": {"cwd": "./path/from/project/root/to/stepup/root/"},
      "presentation": {
        "echo": true,
        "reveal": "silent",
        "focus": false,
        "panel": "shared",
        "showReuseMessage": false,
        "clear": true
      }
    }
  ]
}
```

The following `keybindings.json` file will bind `ctrl+d` to run the task:

```json
[
  {
    "key": "ctrl+d",
    "command": "workbench.action.tasks.runTask",
    "args": "StepUp run"
  }
]
```

"""

from .api import RPC_CLIENT, translate

__all__ = ("cleanup", "graph", "join", "run", "wait", "watch_delete", "watch_update")


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
