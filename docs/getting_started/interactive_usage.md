# Interactive usage

All previous tutorials ran StepUp non-interatively, for the sake of simplicity.
In practice this is mainly useful for build projects in batch jobs, e.g. in the cloud.
When working on a project, interative usage is more efficient and convenient,
but requires a little more explanation.
(For this reason most tutorials here will use the non-interactive option.)

The tutorial [Static glob](static_glob.md) is in fact a good showcase for StepUp's interactive usage.
When running StepUp as follows, the terminal user interface will not exit:

```bash
stepup
```

In fact, `stepup` without any arguments is the recommended way of running StepUp in most cases.

After the line `PHASE │ watch` appears, StepUp just waits for changes to the (static) files.

## Change an existing file

For example, while StepUp is still running, change the file `src/foo.txt`.
You will at least see the following:

```
     ADDED │ src/foo.txt
```

Now go back to the terminal and press the character `?`,
which will show the supported keys with interactive commands:

```
───────────────────────────────────── Keys ─────────────────────────────────────

   q = shutdown       d = drain        j = join   g = graph
   f = from scratch   t = try replay   r = run

────────────────────────────────────────────────────────────────────────────────
```

Now press (lower case) `r` to run steps whose (indirect) inputs have changed.
The new file `src/foo.txt` is copied again to `dst/foo.txt` while other step are ignored.

The interactive commands are described in detail in the [Interactive command reference](../reference/interactive.md)

## Add a new file that matches `glob("src/*.txt")`

Create a new file `src/spam.txt` with some contents to your liking, still while StepUp is running.
You will at least see the following:

```
     ADDED │ src/spam.txt
```

Now press (lower case) `r` again.
The `./plan.py` step is executed again because a new file appeared that matches a glob pattern used in `plan.py`.
Rerunning `./plan.py` will in turn create a new step to copy `src/spam.txt` to `dst/spam.txt`.
