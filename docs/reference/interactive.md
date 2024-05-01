# Interactive Command Reference

By default, StepUp runs in interactive mode.
When pressing a key on the keyboard, it responds by executing a corresponding command.
If the key is not associated with any command, the following help message appears:

```
───────────────────────────────────── Keys ─────────────────────────────────────

   q = shutdown       d = drain        j = join   g = graph
   f = from scratch   t = try replay   r = run

────────────────────────────────────────────────────────────────────────────────
```

These commands are defined as follows:

- `q = shutdown`:
  StepUp waits for the workers to complete their current job and will not start new jobs.
  As soon as all workers are idle, StepUp exits.
- `d = drain`:
  StepUp waits for the workers to complete their current job and will not start new jobs.
  As soon as all workers are idle, StepUp transitions into the *watch phase*.
- `j = join`:
  StepUp continues running jobs until no new jobs can be found to send to the workers.
  As soon as all workers are idle, StepUp terminates.
- `g = graph`:
  Writes out the workflow graph in text format to a file named `graph.txt`.
  (This human-readable file contains most of the information from `.stepup/workflow.mp.xz`)
- `f = from scratch`:
  Discards all hashes and reruns all steps.
  No attempts are made to skip steps, and everything is executed from scratch.
- `t = try replay`:
  Checks the hash of each step and skips it if no changes were detected.
  Otherwise, run the step.
  (Normally, this command is not needed.)
- `r = run`:
  Runs steps that are affected by file changes registered during the *watch phase*.
