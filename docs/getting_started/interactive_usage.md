# Interactive Usage
<!--
SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
SPDX-License-Identifier: CC-BY-SA-4.0
-->

!!! note

    The command-line options related to interactive usage have changed in StepUp 3.0.0

    More detailed information on interactive usage can be found in the
    [Interactive Usage Reference](../reference/interactive.md).

All previous tutorials have run StepUp non-interactively, for the sake of simplicity.
In practice, this is mainly useful when building projects in batch jobs,
e.g., in the cloud or on an HPC cluster.
When working on a project, interactive usage is more efficient and convenient,
and its usage is described below.

The [Glob Patterns in `static()`](static_patterns.md) tutorial is a good example
to demonstrate the interactive use of StepUp.
Running StepUp as follows will not exit the terminal user interface:

```bash
sb -w
```

After the line `PHASE │ watch` appears, StepUp just waits for changes to the (static) files.

## Change an Existing File

For example, while StepUp is still running, edit and save the file `src/foo.txt`.
You will see at least the following:

```text
    UPDATED │ src/foo.txt
```

Now go back to the terminal and press the character `?`
to display the supported keys with interactive commands:

```text
───────────────────────────────────── Keys ─────────────────────────────────────
  g = graph       Write the workflow graph to graph.txt.
  d = drain       Drain the scheduler. (Leaves build phase.)
  j = join        Wait for all steps to complete before shutting down.
  q = shutdown    Shut down the system. (1st is graceful. 2nd kills steps.)
  r = rebuild     Restart the builder. (Leaves watch phase.)
────────────────────────────────────────────────────────────────────────────────
```

Now press (lower case) `r` to run steps whose (indirect) inputs have changed.
This will trigger a refresh/re-copy of files like `src/foo.txt`, while other steps are ignored.

The interactive commands are described in detail
in the [Interactive Command Reference](../reference/interactive.md).

## Add a New File That Matches `static("src/*.txt")`

Create a new file `src/spam.txt` with content of your choice while StepUp is still running.
You will see at least the following:

```text
    UPDATED │ src/spam.txt
```

Now press (lower case) `r` again.
The `./plan.py` step is executed again because a new file has appeared
that matches the glob pattern used in `plan.py`.
Running `./plan.py` again will, in turn, create a new step to copy `src/spam.txt` to `dst/spam.txt`.

## Screen Recording

The following recording shows the terminal output when starting StepUp from scratch
with 2 steps allowed to run in parallel, changing `src/foo.txt` and re-running,
followed by adding `src/spam.txt` and re-running:

<script src="https://asciinema.org/a/718834.js" id="asciicast-718834" async="true"></script>

## Watch mode with automatic re-run

If you prefer to avoid switching back and forth between the terminal and the editor,
you can use the `-W` option instead of `-w`.
This will automatically re-run the steps half a second after the first file change:

```bash
stepup -W
```
