---
description: >-
  Every key that StepUp's terminal user interface responds to,
  and what the -w and -W options change about the build phase.
---

<!--
SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# Interactive Usage Reference

!!! note

    Changes since StepUp 3.0.0:

    - You have to start the StepUp workflow with `sb` instead of `stepup`.

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
  g = graph       Write the workflow graph to graph.txt.
  d = drain       Drain the scheduler. (Leaves build phase.)
  j = join        Wait for all steps to complete before shutting down.
  q = shutdown    Shut down the system. (1st is graceful. 2nd kills steps.)
  r = rebuild     Restart the builder. (Leaves watch phase.)
────────────────────────────────────────────────────────────────────────────────
```

These commands are defined as follows:

- `r = rebuild`:
  Runs steps that are affected by file changes registered during the *watch phase*.
- `q = shutdown`:
  StepUp waits for all running steps to complete and will not start new jobs.
  As soon as no steps are running, StepUp exits.
  If it takes too long for the steps to complete, you can press `q` again to kill them with `SIGINT`.
  Press `q` for a third time to kill the steps with `SIGKILL`. (nuclear option)
- `d = drain`:
  StepUp will not start new jobs and lets the running steps finish.
  As soon as no steps are running, StepUp transitions into the *watch phase*.
- `j = join`:
  StepUp continues running jobs until no new jobs can be found.
  As soon as no steps are running, StepUp terminates.
- `g = graph`:
  Writes out the workflow graph in text format to a file named `graph.txt`.
  (This human-readable file contains most of the information from `.stepup/workflow.mp.xz`)
  Note that the glob patterns registered with a step are labeled `nglob` in this output,
  which is StepUp's internal name for a (possibly named) glob pattern.

Note that these interactive keys also work without the `-w` or `-W` option,
except for `r` which only has an effect during the *watch phase*.

Pressing `Ctrl+C` (or sending `SIGTERM`) also stops StepUp, but more abruptly than `q`:
it aborts the build instead of waiting for running steps.
Every running step is interrupted with `SIGINT`,
and whatever is still running a few seconds later is killed with `SIGKILL`,
so a single `Ctrl+C` is always enough to get your shell prompt back.
(Pressing `Ctrl+C` again just skips the waiting.)
An aborted build sets the `2` bit in the [return code](returncode.md).

## Suspending a Build

Pressing `Ctrl+Z` suspends the build and returns you to the shell prompt,
and `fg` resumes it where it left off, with keyboard interaction still working.

Running steps are suspended along with StepUp itself,
so nothing keeps using CPU or writing files while the build is stopped.
This needs StepUp's cooperation:
steps run in a session of their own (so that a `Ctrl+C` cannot reach them directly),
which also means the terminal cannot suspend them.
The director stops them with `SIGSTOP` and continues them with `SIGCONT`.
The time a step spends suspended is not counted as time it spent working,
so the durations reported for steps stay meaningful.

Two caveats:

- Sending a suspended build to the background with `bg` is not supported.
  StepUp reads the keyboard, and a background process that reads from the terminal
  is stopped again by the operating system.
  If you want to run StepUp in the background,
  do so from the beginning as explained in the next section.
- A step that calls back into StepUp (with `step()`, for instance) at the moment of
  the suspension gives up after `STEPUP_SYNC_RPC_TIMEOUT` seconds (600 by default),
  so a build left suspended for longer than that may report a failed step.
  A few calls waive that timeout because the director answers them only when the workflow
  is ready for it, `amend()` most notably, and those wait for as long as the suspension lasts.

## How Watch Mode Observes Changes

With `--watch` or `--watch-first`, StepUp asks the operating system to report changes
in the directories that hold files of the workflow.
On Linux, it does so with inotify.
On macOS and Windows, it uses the [watchfiles](https://pypi.org/project/watchfiles/) library,
which comes with a few limitations:

- Changes are collected in batches of about 10 milliseconds.
  The changes within one batch are reported in a fixed order,
  which may differ from the order in which they happened.
- When the operating system has to drop changes because too many happen at once,
  StepUp is not told that anything is missing.
  This was observed with watchfiles 1.3.0, which does not pass on such a notice.
  If StepUp seems to have missed changes after a burst of activity,
  e.g. after switching branches in a large repository,
  restart `stepup build`, which checks all files of the workflow when it starts.
- On Windows, a single watch on the directory in which StepUp runs covers everything below it,
  so you can rename and remove directories of the project while StepUp is watching.
  Two exceptions remain, because Windows does not rename a directory
  while a program keeps a directory below it open:
  the parent directories of the directory in which StepUp runs,
  and the parent directories of a watched directory outside of it,
  e.g. `../shared` when the workflow uses `../shared/data/table.csv`.

## Interacting With a Background StepUp Process

You can run StepUp in the background in several ways:

- Just start it with `sb > stepup.log &`
  and then use `tail -f stepup.log` to see the output.
- Run StepUp inside a `screen` or `tmux` session.
- Run StepUp in a Slurm/PBS/... batch job on a cluster.

In all these cases, keyboard interaction is not possible.
However, you can still interact with StepUp as follows:

1. Open a terminal on the machine running StepUp.
2. Use `cd` to go to the directory where StepUp is running.
3. Execute one of the following commands:

    - `stepup rebuild`
    - `stepup shutdown`
    - `stepup drain`
    - `stepup join`
    - `stepup graph`
    - `stepup status` (prints detailed status of the workflow)

## Interacting With StepUp From Within an IDE

If you don't want to switch to a terminal to restart StepUp while working in an IDE,
you can run it in "watch mode" (`sb -w`) and configure your IDE
to bind the following command to a keyboard shortcut:

```bash
stepup rebuild
```

This command must be executed in the top-level directory
where a `sb` command is running in interactive mode.
(You can also set the `STEPUP_ROOT` environment variable instead.)

### Configuration of a Task in VSCode

You can define a
[Custom Task in VSCode](https://code.visualstudio.com/docs/editor/tasks#_custom-tasks)
to start the build phase of a StepUp instance running in a terminal.

For this example, we will assume the following:

- You have an `.envrc` file that defines the environment variable `STEPUP_ROOT`
  and you have configured and installed [direnv](https://direnv.net/).
- You have an interactive StepUp instance running in a terminal (with `stepup -w`).
- You want to use the `ctrl+'` keybinding to start the build phase
  while you are editing a file in the StepUp project.

Add the following to your user `tasks.json` file:

```json
{
  "version": "2.0.0",
  "tasks": [
    {
      "label": "StepUp rebuild",
      "type": "shell",
      "command": "eval \\"$(direnv export bash)\\"; stepup rebuild",
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
    "args": "StepUp rebuild"
  }
]
```

VSCode will automatically save the file when you run the task with this keybinding.

Instead of this shortcut, you can also use `sb -W`,
which will automatically rerun the build as soon as you delete, save or add a relevant file.
