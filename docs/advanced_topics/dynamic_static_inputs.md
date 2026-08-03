# Dynamic Static Inputs
<!--
SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
SPDX-License-Identifier: CC-BY-SA-4.0
-->

The title of this page is a perfectly legit oxymoron in StepUp:
it is possible to declare a static input file and then amend the same step
with that file as a dynamic input, all in the same script.
It is a special case of [Dynamic Dependencies](dynamic_dependencies.md),
which is occasionally useful.
As of StepUp 1.2.0, this is allowed and no longer treated as a cyclic dependency.

For example, `plan.py` may read a configuration file to decide which steps to add to the workflow.
Hence, the config file is a static input to the workflow.
Since StepUp schedules the top-level `./plan.py` step without initial inputs,
you have to use `amend()` to inform StepUp of this dependency.
Whenever you change the config file, StepUp will re-run `./plan.py` to update the workflow.

## Example

Create the following `plan.py`, which declares a static file,
amends the step with that file as a dynamic input,
and then opens it to print it to the standard output.

```python
{% include 'advanced_topics/dynamic_static_inputs/plan.py' %}
```

Also create a `config.txt` file with some contents.

In more realistic scenarios, `config.txt` may be used to decide which steps to add etc.
For a more elaborate example, take a look at the
[`plan.py`](https://github.com/reproducible-reporting/stepup-core/blob/main/docs/plan.py)
that is used to run all tutorial examples.

Make `plan.py` executable and run it as follows:

```bash
chmod +x plan.py
sb -j 1
```

You should get the following terminal output:

```text
{% include 'advanced_topics/dynamic_static_inputs/stdout.txt' %}
```
