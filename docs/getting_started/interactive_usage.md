# Interactive Usage

All previous tutorials have run StepUp non-interactively, for the sake of simplicity.
In practice, this is mainly useful when building projects in batch jobs, e.g., in the cloud.
When working on a project, interactive usage is more efficient and convenient,
but requires a little more explanation.
(For this reason, most of the tutorials use the non-interactive option.)

The [Static Glob](static_glob.md) tutorial is a good example to demonstrate the interactive use of StepUp.
Running StepUp as follows will not exit the terminal user interface:

```bash
stepup
```

In fact, running the `stepup` command without any arguments is the recommended way to run StepUp in most cases.

After the line `PHASE │ watch` appears, StepUp just waits for changes to the (static) files.


## Change an Existing File

For example, while StepUp is still running, edit and save the file `src/foo.txt`.
You will see at least the following:

```
    UPDATED │ src/foo.txt
```

Now go back to the terminal and press the character `?`
to display the supported keys with interactive commands:

```
───────────────────────────────────── Keys ─────────────────────────────────────

   q = shutdown       d = drain        j = join   g = graph
   f = from scratch   t = try replay   r = run

────────────────────────────────────────────────────────────────────────────────
```

Now press (lower case) `r` to run steps whose (indirect) inputs have changed.
The new file `src/foo.txt` is copied again to `dst/foo.txt`, while other steps are ignored.

The interactive commands are described in detail in the [Interactive Command Reference](../reference/interactive.md).


## Add a New File That Matches `glob("src/*.txt")`

Create a new file `src/spam.txt` with content of your choice while StepUp is still running.
You will see at least the following:

```
    UPDATED │ src/spam.txt
```

Now press (lower case) `r` again.
The `./plan.py` step is executed again because a new file has appeared that matches a glob pattern used in `plan.py`.
Running `./plan.py` again will, in turn, create a new step to copy `src/spam.txt` to `dst/spam.txt`.


## Screen Recording

The following recording shows the terminal output when starting StepUp from scratch with two workers, changing `src/foo.txt` and re-running, followed by adding `src/spam.txt` and re-running:

[![asciicast](https://asciinema.org/a/657277.svg)](https://asciinema.org/a/657277)
