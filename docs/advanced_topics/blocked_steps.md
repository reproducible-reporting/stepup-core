# Blocked Steps

<!--
SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
SPDX-License-Identifier: CC-BY-SA-4.0
-->

As discussed in a previous tutorials on [Optional Steps](optional_steps.md) and [Build Targets](build_targets.md),
StepUp has several mechanisms to ignore certain steps.
As a rule, however, StepUp will always try to execute all steps, and not doing so is the exception.

A valid reason for ignoring some steps is illustrated in the following schematic:

```text
     File          In development           File                Costly
╔═════════════╗     ┌──────────┐     ╔═════════════════╗     ┌──────────┐
║  input.txt  ║ --> │  Step 1  │ --> ║  converted.txt  ║ --> │  Step 2  │
╚═════════════╝     └──────────┘     ╚═════════════════╝     └──────────┘
```

Imagine that `Step 2` is very expensive and you are developing a script for `Step 1`.
In practice, it takes several iterations to get `Step 1` working properly.
This can be verified by analyzing the file `converted.txt` or by running unit tests.

To avoid executing `Step 2` at every iteration in the development of `Step 1`,
you can **block** this step.
Blocking is achieved by assigning an undefined resource to the step,
e.g. `resources="gate"`.
Because the scheduler does not know about a resource named `gate`,
the step remains permanently pending until you remove the argument.
Blocked steps are intended to be a temporary measure,
and to be reverted once you're done with `Step 1`.

Blocking a step has some consequences:

- A blocked step remains in the `PENDING` state,
  meaning that outdated output files are not cleaned up automatically.
- At the end of the **build phase**, blocked steps are reported as a reminder,
  grouped by resource name with a count of how many steps each resource blocks.
  (The same grouping applies to steps blocked on unavailable inputs, grouped by file.)
  These counts are per-root totals: a step blocked on two different roots is counted
  under both, so the counts across the report can add up to more than the total
  number of pending steps.
  When [build targets](build_targets.md) are in use and the blocked step is not needed
  to produce any of the given targets, it stays silently `PENDING` like any other unneeded step.
- Subsequent steps, which use outputs of blocked or pending steps, also remain pending.

## Example

Example source files: [`docs/advanced_topics/blocked_steps/`](https://github.com/reproducible-reporting/stepup-core/tree/main/docs/advanced_topics/blocked_steps)

The following `plan.py` illustrates the blocking mechanism.
Note that the copy commands are too cheap to justify blocking,
so this is just an example illustrating the mechanism.

```python
{% include 'advanced_topics/blocked_steps/plan.py' %}
```

Make this plan executable and run it with StepUp:

```bash
chmod +x plan.py
sb -j 1
```

You should get the following terminal output:

```text
{% include 'advanced_topics/blocked_steps/stdout.txt' %}
```

## Try the Following

- Run `sb -r gate` to provide the `gate` resource and allow the blocked step to run.
  The output files `b.txt` and `c.txt` will be created.

- Next, run `sb` without the `gate` resource.
  Although the copy commands are not executed, obviously,
  but their outputs (`b.txt` and `c.txt`) are not cleaned up either.
  This is the expected behavior because automatic cleaning is only performed when all
  (non-optional) steps have been executed successfully.

- Remove the `resources="gate"` argument and then make the last copy command optional.
  Rerun `sb` and the output of the optional step (`c.txt`) will be removed.
  Because all non-optional steps have been executed successfully,
  the automatic cleaning mechanism was triggered.
