# Duration and Hold
<!--
SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
SPDX-License-Identifier: CC-BY-SA-4.0
-->

By default,
the scheduler prioritizes eligible steps by *tail time*,
i.e. the longest time path from that step to any terminal node of the workflow graph.

Steps with longer tail times are dispatched first, in the spirit of
[critical-path scheduling](https://en.wikipedia.org/wiki/Critical_path_method),
which generally results in the shortest overall execution time.
A step's own contribution to its tail time is its `duration`,
an estimate of its wall time in seconds.
This works well once all step durations are known,
which requires that the workflow has run at least once,
or that the durations have been estimated and passed in explicitly.

For the first execution of a workflow from scratch, StepUp has no measured durations.
A newly declared step defaults to a `duration` of `1.0` second,
which is a poor estimate for a step that actually takes a few milliseconds or several minutes.
You can pass a better estimate explicitly with the `duration` keyword argument,
accepted by [`step()`][stepup.core.api.step] and all step-generating API functions
(`run()`, `script()`, `call()`, `render_jinja()`, etc.).
Because it is only used to break ties before the first measurement exists,
there is no need to keep it accurate:
a rough guess ("a few seconds" vs. "a few minutes") is already enough
to help the scheduler make good decisions on a clean build.

At the end of a build, unless the `--no-duration` option is set,
StepUp records the measured wall time of each step in the workflow graph database,
so that the next build can use those measurements to schedule more efficiently.
These measurements overwrite any user-specified `duration` arguments,
which are only used for steps that have never run before.

A well-chosen `duration` is not always enough on its own, though.
The scheduler can only prioritize among steps that are currently *eligible* for dispatch,
and steps become eligible one by one, as soon as they are declared with no missing inputs.
When a `plan.py` declares several such steps in a row and a job slot is free,
the first one declared can grab that slot immediately,
regardless of how long it or the steps declared after it are expected to take.
The [`hold()`][stepup.core.api.hold] context manager solves this
by holding back the steps declared inside its block:
none of them become eligible until the block exits,
so the whole batch is compared by tail time at once,
instead of being raced by declaration order.
`hold()` is re-entrant, so nesting `with hold():` blocks
(directly, or through a shared helper function) is safe;
children only become eligible once the outermost block exits.

## Example

Example source files: [`docs/advanced_topics/duration_and_hold/`](https://github.com/reproducible-reporting/stepup-core/tree/main/docs/advanced_topics/duration_and_hold)

The `plan.py` below declares four steps with three job slots available (`-j 3`):
two fast ones (2.0 and 2.1 seconds) and two slow ones (4.0 and 4.1 seconds),
in that order, i.e. the two slow steps are declared last.
`plan.py` itself occupies one of the three slots while it runs,
leaving two slots free.

Without `hold()`, the two fast steps, declared first, immediately grab those two free slots,
before the scheduler even knows the slow steps exist.
By the time the slow steps are declared, no slot is left for them:
one gets the slot `plan.py` frees up once it finishes,
but the other has to wait for a fast step to finish too.
As a result, one of the two slow steps is delayed
and ends up running alone at the end of the build.

Wrapping the declarations in `hold()`, and giving each step a `duration` estimate,
makes the scheduler compare all four by tail time as soon as the block releases,
so both free slots go to the slow steps instead, even though they were declared last.

```python
{% include 'advanced_topics/duration_and_hold/plan.py' %}
```

To run the example, make the plan executable and run it:

```bash
chmod +x plan.py
sb -j 3
```

You should get the following terminal output:

```text
{% include 'advanced_topics/duration_and_hold/stdout.txt' %}
```

Notice that **both slow steps start together, right alongside `plan.py` itself**,
before `plan.py` even reports success.
Only afterward the two fast steps run, one at a time,
in the single slot `plan.py` leaves behind once it finishes.
The whole build takes a bit over four seconds,
essentially just the two slow steps running in parallel,
with the two fast steps fitting into the leftover capacity.

## Try the Following

- Remove the `with hold():` block (keep the four `run()` calls, unindented),
  remove `.stepup/graph.db` to force a clean build, and rerun `sb -j 3`.
  Now the two fast steps both start immediately, grabbing the two slots that are free
  while `plan.py` is still running, before either slow step even exists
  in the workflow graph.
  Once `plan.py`'s own slot frees up, the slowest step (4.1 seconds) starts,
  as this is the one with the longest tail time.
  As soon as one of the two fast steps finishes, the other slow step (4.0 seconds) starts.
  The build now takes a bit over six seconds,
  two seconds slower than with `hold()`,
  purely because of the order in which the steps happened to be declared.

- With `hold()` restored, remove `.stepup/graph.db` and run `sb -j 3` again.
  You should get the same output as in the example above.

- Run `sb -j 3` once more, without removing anything this time.
  All four steps are skipped, since nothing changed.

- Change one of the `sleep 4.1` commands, e.g. to `sleep 4.2`, and rerun `sb -j 3`.
  Only that step reruns, since only its command changed.
