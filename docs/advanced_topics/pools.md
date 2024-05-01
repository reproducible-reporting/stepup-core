# Pools

It is nearly always preferred to run steps in parallel when possible,
so StepUp will launch any queued step as soon as a worker becomes available.
A "pool" is a simple mechanism to limit parallelization in the few cases that this would be counterproductive:

1. Some programs behave poorly (have bugs) when multiple instances are running in parallel.
   Here are a few examples encountered in the development of StepUp RepRep:
    - [Inkscape/issue4716](https://gitlab.com/inkscape/inkscape/-/issues/4716)
    - [markdown-katex/issue16](https://github.com/mbarkhau/markdown-katex/issues/16)

2. Some steps may consume a lot of resources, such as memory,
   and would require more resources than available when running in parallel.

3. Software licenses may not allow for more than a given number of instances running in parallel.

One defines a pool with [`pool(name, size)`][stepup.core.api.pool].
The size is the maximum number of steps running concurrently within the pool.
Steps are assigned to a pool by defining them with the `pool=name` keyword argument.

For StepUp, mainly the first use case (working around concurrency bugs) is relevant,
for which the pool size is 1.


## Example

Example source files: [advanced_topics/pools/](https://github.com/reproducible-reporting/stepup-core/tree/main/docs/advanced_topics/pools)

The example here illustrates the use of a pool in a simple test case.
The steps can run easily in parallel, so you can experiment with the pool size.

Create the following `plan.py`:

```python
{% include 'advanced_topics/pools/plan.py' %}
```

The `sleep` command ensures that each step lasts long enough to guarantee they will run in parallel when allowed.


Make the plan executable and run it with StepUp:

```bash
chmod +x plan.py
stepup -n -w4
```

You should get the following output:

```
{% include 'advanced_topics/pools/stdout.txt' %}
```

Initially, the `./plan.py` step and two `sleep+echo` commands are running in parallel.
Despite having four workers,
the third `sleep+echo` is only started after the previous two have finished.


## Try the Following

- Run `stepup -n -w4` again without making changes.
  Skipping of steps requires some computation and comparison of hashes,
  which is done by worker processes.
  However, these hash computations are never subject to pool size restrictions.

- Change the pool size to `1` or `3` and verify that the output matches your expectations.
  When you try this, StepUp will continue skipping steps.
  To forcibly re-execute steps, you have two options:

    1. Remove the file `.stepup/workflow.mpk.xz` and start StepUp.
    2. Run StepUp interactively (without `-n`) and use the `f` key to start the workflow from scratch.
