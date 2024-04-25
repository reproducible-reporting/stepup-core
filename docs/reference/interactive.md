# Interactive command reference

By default StepUp runs in interactive mode.
When pressing a key on the keyboard, it will respond by executing a corresponding command.
If the key is not associated with some command, the following help message is shown:

```
───────────────────────────────────── Keys ─────────────────────────────────────

   q = shutdown       d = drain        j = join   g = graph
   f = from scratch   t = try replay   r = run

────────────────────────────────────────────────────────────────────────────────
```

These commands are defined as follows:

- `q = shutdown`:
  StepUp waits for the workers to complete their current job but will not start new jobs.
  As soon as all workers are idle, StepUp exists.
- `d = drain`:
  StepUp waits for the workers to complete their current job but will not start new jobs.
  As soon as all workers are idle, StepUp switches to *watch phase*.
- `j = join`:
  StepUp keeps running jobs until no new jobs can be found to send to the workers.
  As soon as all workers are idle, StepUp exists.
- `g = graph`:
  Write out the workflow graph in text format to a file `graph.txt`.
  (This is a human readable file containing most of the information in `.stepup/workflow.mp.xz`)
- `f = from scratch`:
  Discard all hashes and rerun all steps.
  No attempts are made to skip steps and everything is executed from scratch.
- `t = try replay`:
  Check the hash of each step and skip it if no changes were detected. Run otherwise.
  (Normally, this command is never needed.)
- `r = run`:
  Run steps that are affected by file changes registered in *watch phase*.
