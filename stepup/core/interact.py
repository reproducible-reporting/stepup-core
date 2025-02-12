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

    (When StepUp runs `plan.py` scripts, they also use this environment variable
    to interact with the director process.
    Because these are subprocesses of the director,
    the `STEPUP_DIRECTOR_SOCKET` is set by the director.)

## Configuration of a Task in VSCode

You can define a
[Custom Task in VSCode](https://code.visualstudio.com/docs/editor/tasks#_custom-tasks)
to start the run phase of a StepUp instance running in a terminal.

For this example, we will assume the following:

- You have an `.envrc` file that defines the environment variable `STEPUP_ROOT`
  and you have configured and installed [direnv](https://direnv.net/).
- You have an interactive StepUp instance running in a terminal (with `stepup -w`).
- You want to use the `ctrl+'` keybinding to start the run phase
  while you are editing a file in the StepUp project.

Add the following to your user `tasks.json` file:

```json
{
  "version": "2.0.0",
  "tasks": [
    {
      "label": "StepUp run",
      "type": "shell",
      "command": "eval \\"$(direnv export bash)\\"; \
STEPUP_DIRECTOR_SOCKET=$(python -c 'import stepup.core.director; \
print(stepup.core.director.get_socket())') \
python -c 'from stepup.core.interact import run; run()'",
      "options": {
        "cwd": "${fileDirname}"
      },
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

This will create a task that executes the command in the directory of the file you are editing.
With `eval \"$(direnv export bash)\"`, the environment variables from your `.envrc` file are loaded.
The rest of the `command` field is the same as the command we used in the first example.

The following `keybindings.json` file will bind `ctrl+'` to run the task:

```json
[
  {
    "key": "ctrl+'",
    "command": "workbench.action.tasks.runTask",
    "args": "StepUp run"
  }
]
```

VSCode will automatically save the file when you run the task with this keybinding.

Instead of this shortcut, you can also use `stepup -W`,
which will automatically rerun the build as soon as you delete, save or add a relevant file.

"""

from .api import RPC_CLIENT

__all__ = ("graph", "join", "run", "wait", "watch_delete", "watch_update")


def run():
    """Exit the watch phase and start running steps whose inputs have changed."""
    RPC_CLIENT.call.run()


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
