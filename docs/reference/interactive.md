# Interactive Command Reference

!!! note

    Changes as of StepUp 2.0.0:

    - The command-line options related to interactive usage have changed.
    - Keyboard interaction is always available, irrespective of the command-line options.
    - The `f` and `t` keys have been removed.

By default, StepUp performs a single pass execution of the workflow.
You can use StepUp interactively by adding
`-w` (manual re-run) or `-W` (automatic re-run) to the command line.
When pressing a key on the keyboard, StepUp responds by executing a corresponding command.
If the key is not associated with any command, the following help message appears:

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

Note that `SIGINT` (pressing `Ctrl+C`) and `SIGTERM` (sending a `kill` signal)
are also supported to stop StepUp.
