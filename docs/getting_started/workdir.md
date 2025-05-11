# Working Directory

Every step is executed in a working directory, which you can specify when creating a step.
If the arguments `inp`, `out` and `vol` contain relative paths,
they are assumed to be relative to the working directory.

!!! warning

    StepUp assumes that the current working directory is not changed
    between importing any `stepup` module and calling functions from `stepup.core.api`

## Example

Example source files: [`docs/getting_started/workdir/`](https://github.com/reproducible-reporting/stepup-core/tree/main/docs/getting_started/workdir)

Create a top-level `plan.py` as follows:

```python
{% include 'getting_started/workdir/plan.py' %}
```

Make the scripts executable and run everything as follows:

```bash
chmod +x plan.py
mkdir out/
stepup boot -n 1
```

You should get the following terminal output:

```text
{% include 'getting_started/workdir/stdout.txt' %}
```

This will create an output file `out/hello.txt`

## Further Reading

- The [`step()`][stepup.core.api.step] function
  and related functions that use `step()` internally
  ([`runsh()`][stepup.core.api.runsh], [`plan()`][stepup.core.api.plan],
  [`script()`][stepup.core.api.script], [`call()`][stepup.core.api.call], ...)
  all return a [`StepInfo`][stepup.core.stepinfo.StepInfo] object,
  which may be used for defining follow-up steps.
  The `StepInfo` objects follow the same convention:
  their `inp`, `out` and `vol` attributes are lists of paths.
  If these are relative paths, they are relative to the `step_info.workdir` attribute.
  Consult the section [StepInfo Objects](../advanced_topics/step_info.md) for more details.

- In advanced workflows, the [HERE and ROOT variables](../advanced_topics/here_and_root.md)
  can be convenient to construct relative paths based on the current working directory.
