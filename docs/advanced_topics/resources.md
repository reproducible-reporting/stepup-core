# Resources

It is nearly always preferred to run steps in parallel when possible,
so StepUp will launch any queued step as soon as a worker becomes available.
A "resource" is a named quantity that limits how many steps can run concurrently
in cases where full parallelization would be counterproductive:

1. Some programs behave poorly (have bugs) when multiple instances are running in parallel.
   Here are a few examples encountered (and now also resolved) in the development of StepUp RepRep:

    - [Inkscape/issue4716](https://gitlab.com/inkscape/inkscape/-/issues/4716)

    - [markdown-katex/issue16](https://github.com/mbarkhau/markdown-katex/issues/16)

2. Some steps may consume a lot of resources, such as memory or GPU compute,
   requiring more resources than available when running in parallel.

3. Some software licenses may limit the number of instances running in parallel.

Available resources are declared via the `STEPUP_RESOURCES` environment variable
or the `--resources` CLI argument (CLI values take precedence).
Both accept a comma-separated list of `name:quantity` pairs,
e.g. `STEPUP_RESOURCES="cpu:4,gpu:1,memgb:16"`.

Steps declare which resources they need with the `resources` keyword argument,
which accepts either a dict (e.g. `{"gpu": 1}`) or a shorthand string (e.g. `"gpu:1"` or `"gpu"`).
Resources not listed in `STEPUP_RESOURCES` or `--resources` are treated as unavailable,
so any step requiring them will never run.
Requesting non-positive quantity of a resource (e.g. `resources={"gpu": 0}`)
is not allowed and will raise an error,
but one can specify a resource with zero quantity in `STEPUP_RESOURCES`
to make it unavailable (e.g. `STEPUP_RESOURCES="gpu:0"`).
More details can be found in the [`step()`][stepup.core.api.step] API documentation.

## Example

Example source files: [`docs/advanced_topics/resources/`](https://github.com/reproducible-reporting/stepup-core/tree/main/docs/advanced_topics/resources)

The example illustrates three steps with different resource requirements.

Create the following `plan.py`:

```python
{% include 'advanced_topics/resources/plan.py' %}
```

The `sleep` command ensures each step lasts long enough to guarantee
they will run in parallel when allowed.

Step C requires 2 CPUs and 1 GPU simultaneously, so it can only start
once both A (which holds 1 CPU) and B (which holds 1 GPU) have finished.

Set the environment variable and run StepUp with four workers:

```bash
export STEPUP_RESOURCES="cpu:2,gpu:1"
chmod +x plan.py
stepup boot -n 4
```

You should get the following output:

```text
{% include 'advanced_topics/resources/stdout.txt' %}
```

Steps A and B start immediately in parallel.
Despite having four workers, step C only starts after both A and B have finished,
because it needs 2 CPUs and 1 GPU at the same time.

## Try the Following

- Run `stepup boot -n 4` again without making changes.
  Skipping steps requires hash computations, which are done by a dedicated hashing subprocess
  and are never subject to resource restrictions.

- Change `STEPUP_RESOURCES` to `"cpu:4,gpu:2"` and verify that all three steps
  can now run in parallel.
  When you try this, StepUp will continue skipping steps.
  To forcibly re-execute steps, remove the file `.stepup/graph.db` and restart StepUp.

- Remove a resource from `STEPUP_RESOURCES` (e.g. set it to `"cpu:2"`) and observe
  that steps requiring the missing resource are never started and remain pending.
