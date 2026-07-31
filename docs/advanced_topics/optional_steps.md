<!--
SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
SPDX-License-Identifier: CC-BY-SA-4.0
-->
# Optional Steps

By default, StepUp will build all steps.
As an exception, steps can be made optional by adding the `optional=True` option.
This is the opposite of most build tools,
where a step only runs when it is needed, directly or indirectly,
by a *target* given on the command line.

The reason for this difference is that conventional build tools work with rigid predefined graphs.
They introduce some flexibility
by allowing users to specify which steps are *targets* on the command line.
This gives the user some control over which part of the graph is executed,
but also shifts to them the responsibility of knowing which targets a given task actually needs.

StepUp keeps this responsibility with the build tool instead.
The basic premise is that all outdated or missing outputs need to be (re)built,
and it is StepUp's job to figure out which steps that requires.
That said, several legitimate mechanisms exist
for running only part of the workflow when this is genuinely useful.
These are supported by StepUp as follows:

- One can define **steps conditionally**, e.g.,
  as in the tutorial [Glob Conditional](../getting_started/glob_conditional.md).
  Such conditionals are controlled by external factors and
  are picked up by your `plan.py` without manual interventions.

- One can make **steps optional**, as in this tutorial.
  This is useful when multiple steps are defined in a loop,
  as in the [Glob Patterns in `static()`](../getting_started/static_patterns.md) tutorial,
  of which not all steps are required for the end result.
  Use this feature wisely:
  Defining thousands of steps when only a few are actually used, is obviously inefficient.

- One can **build a subset of targets**,
  by listing one or more output paths on the command line,
  as shown in [the next tutorial](build_targets.md).
  This is an *inclusion* mechanism:
  only the steps needed to produce the given targets run, and the rest stay `PENDING`.
  It is useful when you temporarily want to work on
  a small part of a much larger workflow, e.g., for debugging purposes.
  An exact-file target reaches an `optional` step just like any other step:
  naming its output explicitly is enough to build it, regardless of its declared need.
  A [directory target](build_targets.md#directory-targets) is more conservative:
  it only elevates steps whose declared need is `DEFAULT`,
  so an `optional` step's output sitting under a targeted directory is not, by itself,
  a reason to build it.

- One can also **block steps**, as shown in [a later tutorial](blocked_steps.md).
  This acts more as a longer-term *exclusion* filter:
  it keeps one or more steps pending indefinitely,
  e.g., while a downstream part of the workflow is still under development
  or too expensive to run on every iteration,
  until you explicitly unblock them again.

Steps that define other steps, declare static files, or otherwise extend the workflow,
should not be made optional.
Until such steps are executed, StepUp has no idea what output these steps will generate,
which it would need to decide that an optional step is required by another step.

## Example

Example source files: [`docs/advanced_topics/optional_steps/`](https://github.com/reproducible-reporting/stepup-core/tree/main/docs/advanced_topics/optional_steps)

The example below uses the [`call()`][stepup.core.api.call] feature introduced in
the [Call](../getting_started/call.md) tutorial
to create a somewhat entertaining example.
However, practically all step-generating functions support the `optional` argument,
and can thus be made optional in the same way.

Create a first script `generate.py` that generates sequences
of the [logistic map](https://en.wikipedia.org/wiki/Logistic_map)
for different values of the parameter *r*:

```python
{% include 'advanced_topics/optional_steps/generate.py' %}
```

Then, write a `plot.py` script that plots only one of these sequences:

```python
{% include 'advanced_topics/optional_steps/plot.py' %}
```

The `plan.py` file adds steps for both scripts, but makes the data generation optional:

```python
{% include 'advanced_topics/optional_steps/plan.py' %}
```

Finally, make the scripts executable and run StepUp:

```bash
chmod +x generate.py plot.py plan.py
sb -j 1
```

You should get the following output:

```text
{% include 'advanced_topics/optional_steps/stdout.txt' %}
```

Note that, in this case, it would be trivial to modify the `generate.py` script
to only generate the sequence of interest.
Whenever such a simpler approach is possible, it is always preferable.
However, in more complex use cases, it is not always possible
to figure out which steps are going to be needed or not.
In such situations, optional steps can be convenient.

## Try the Following

- Remove the `optional=True` keyword argument from the `call()` for `generate.py` in `plan.py`
  and rerun the plan.
  As expected, additional text files with sequences will be created.

- Restore the `optional=True` keyword argument and rerun the plan.
  As expected, the [Automatic Cleaning](../getting_started/automatic_cleaning.md) feature
  removes the outputs that were generated by steps that are no longer present in the workflow.
