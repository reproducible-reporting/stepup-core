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
"""Collection of tools to interact with the StepUp director.

Most of these tools are used for testing purposes
They can also be employed to create keyboard shortcuts within your IDE,
or to interact with StepUp running in the background on a remote server.

For example, one may bind the following command to an IDE's keyboard shortcut:

```bash
stepup run
```

This command must be executed in the top-level directory
where a `stepup` command is running in interactive mode.
(One may also set the environment variable `STEPUP_ROOT` instead.)

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
      "command": "eval \\"$(direnv export bash)\\"; stepup run",
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

import argparse

from rich import print  # noqa: A004

from .api import get_rpc_client
from .director import get_socket
from .file import FileState
from .step import StepState

__all__ = ()


def shutdown(args: argparse.Namespace):
    """Stop the director."""
    get_rpc_client(get_socket()).call.shutdown()


def shutdown_tool(subparser: argparse.ArgumentParser) -> callable:
    subparser.add_parser(
        "shutdown",
        help="Stop the director. Call again to kill running steps.",
    )
    return shutdown


def join(args: argparse.Namespace):
    """Wait for the runner to become idle and stop the director.

    This is the same as `wait()` followed by `shutdown()`."""
    get_rpc_client(get_socket()).call.join(_rpc_timeout=-1)


def join_tool(subparser: argparse.ArgumentParser) -> callable:
    subparser.add_parser(
        "join",
        help="Wait for the runner to become idle and stop the director.",
    )
    return join


def graph(args: argparse.Namespace):
    """Write the workflow graph files in text and dot formats."""
    get_rpc_client(get_socket()).call.graph(args.prefix)


def graph_tool(subparser: argparse.ArgumentParser) -> callable:
    parser = subparser.add_parser(
        "graph",
        help="Write the workflow graph files in text and dot formats.",
    )
    parser.add_argument(
        "prefix",
        help="Prefix for the output files. The files will be named <prefix>.txt and <prefix>.dot.",
    )
    return graph


def status(args: argparse.Namespace):
    """Print the status of the director."""
    status = get_rpc_client(get_socket()).call.status()
    print("[bold underline]Step counts[/]")
    for value, count in status["step_counts"].items():
        print(f"  {StepState(value).name:10s} {count:6d}")
    print()
    print("[bold underline]File counts[/]")
    for value, count in status["file_counts"].items():
        print(f"  {FileState(value).name:10s} {count:6d}")
    print()
    print("[bold underline]Running steps[/]")
    for action in status["running_steps"]:
        print(f"  {action}")


def status_tool(subparser: argparse.ArgumentParser) -> callable:
    subparser.add_parser(
        "status",
        help="Print the status of the director.",
    )
    return status


def run(args: argparse.Namespace):
    """Exit the watch phase and start running steps whose inputs have changed."""
    get_rpc_client(get_socket()).call.run()


def run_tool(subparser: argparse.ArgumentParser) -> callable:
    subparser.add_parser(
        "run",
        help="Exit the watch phase and start running steps whose inputs have changed.",
    )
    return run


def watch_update(args: argparse.Namespace):
    """Block until the watcher has observed an update of the file."""
    get_rpc_client(get_socket()).call.watch_update(args.path, _rpc_timeout=-1)


def watch_update_tool(subparser: argparse.ArgumentParser) -> callable:
    parser = subparser.add_parser(
        "watch-update",
        help="Block until the watcher has observed an update of the file.",
    )
    parser.add_argument(
        "path",
        help="Path to the file to watch.",
    )
    return watch_update


def watch_delete(args: argparse.Namespace):
    """Block until the watcher has observed the deletion of the file."""
    get_rpc_client(get_socket()).call.watch_delete(args.path, _rpc_timeout=-1)


def watch_delete_tool(subparser: argparse.ArgumentParser) -> callable:
    parser = subparser.add_parser(
        "watch-delete",
        help="Block until the watcher has observed the deletion of the file.",
    )
    parser.add_argument(
        "path",
        help="Path to the file to watch.",
    )
    return watch_delete


def wait(args: argparse.Namespace):
    """Block until the runner has become idle."""
    get_rpc_client(get_socket()).call.wait(_rpc_timeout=-1)


def wait_tool(subparser: argparse.ArgumentParser) -> callable:
    subparser.add_parser(
        "wait",
        help="Block until the runner has become idle.",
    )
    return wait
