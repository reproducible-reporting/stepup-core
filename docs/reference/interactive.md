# Interactive Usage Reference

!!! note

    Changes since StepUp 3.0.0:

    - You have to start the StepUp workflow with `stepup boot` instead of `stepup`.

    Changes since StepUp 2.0.0:

    - The command line options related to interactive usage have changed.
    - Keyboard interaction is always available, regardless of the command-line options.
    - The `f` and `t` keys have been removed.

## Terminal User Interface

By default, StepUp performs a single pass execution of the workflow.
You can use StepUp interactively by adding
`-w` (manual re-run) or `-W` (automatic re-run) to the command line.
When a key is pressed on the keyboard, StepUp responds by executing a corresponding command.
If the key is not associated with a command, the following help message appears:

```text
───────────────────────────────────── Keys ─────────────────────────────────────

   r = run     q = shutdown     d = drain     j = join     g = graph

────────────────────────────────────────────────────────────────────────────────
```

These commands are defined as follows:

- `r = run`:
  Runs steps that are affected by file changes registered during the *watch phase*.
- `q = shutdown`:
  StepUp waits for the workers to complete their current job and will not start new jobs.
  As soon as all workers are idle, StepUp exits.
  If it takes to long for the steps to complete, you can press `q` again to kill them with `SIGINT`.
  Pres `q` for a third time to kill the steps with `SIGKILL`. (nuclear option)
- `d = drain`:
  StepUp waits for the workers to complete their current job and will not start new jobs.
  As soon as all workers are idle, StepUp transitions into the *watch phase*.
- `j = join`:
  StepUp continues running jobs until no new jobs can be found to send to the workers.
  As soon as all workers are idle, StepUp terminates.
- `g = graph`:
  Writes out the workflow graph in text format to a file named `graph.txt`.
  (This human-readable file contains most of the information from `.stepup/workflow.mp.xz`)

Note that these interactive keys also work without the `-w` or `-W` option,
except for `r` which only has an effect during the *watch phase*.

Note that the `SIGINT` signal (pressing `Ctrl+C`) are also supported to stop StepUp.

## Interacting With a Background StepUp Process

You can run StepUp in the background in several ways:

- Just start it with `stepup boot > stepup.log &`
  and then use `tail -f stepup.log` to see the output.
- Run StepUp inside a `screen` or `tmux` session.
- Run StepUp in a Slurm/PBS/... batch job on a cluster.

In all these cases, keyboard interaction is not possible.
However, you can still interact with StepUp as follows:

1. Open a terminal on the machine running StepUp.
2. Use `cd` to go to the directory where StepUp is running.
3. Execute one of the following commands:

    - `stepup run`
    - `stepup shutdown`
    - `stepup drain`
    - `stepup join`
    - `stepup graph`
    - `stepup status` (prints detailed status of the workflow)

## Interacting With StepUp From Within an IDE

If you don't want to switch to a terminal to restart StepUp while working in an IDE,
you can run it in "watch mode" (`stepup boot -w`) and configure your IDE
to bind the following command to a keyboard shortcut:

```bash
stepup run
```

This command must be executed in the top-level directory
where a `stepup boot` command is running in interactive mode.
(You can also set the `STEPUP_ROOT` environment variable instead.)

### Configuration of a Task in VSCode

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

Instead of this shortcut, you can also use `stepup boot -W`,
which will automatically rerun the build as soon as you delete, save or add a relevant file.
