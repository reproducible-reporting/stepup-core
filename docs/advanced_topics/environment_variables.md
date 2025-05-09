# Environment Variables

When defining a step, one can specify the environment variables it uses (not their values).
When restarting StepUp with a different value for any of these variables,
StepUp will know that it has to rerun the step instead of skipping it,
even if input files have not changed.

One can only change an environment variable by stopping StepUp,
changing the variable, and then starting StepUp again.
One cannot modify environment variables while StepUp is running.

## Example

Example source files: [`docs/advanced_topics/environment_variables/`](https://github.com/reproducible-reporting/stepup-core/tree/main/docs/advanced_topics/environment_variables)

Create the following `plan.py`:

```python
{% include 'advanced_topics/environment_variables/plan.py' %}
```

Make it executable and run StepUp with a specific value of the variable:

```bash
chmod +x plan.py
MYVAR=foo stepup boot -n 1
```

You will see the following output:

```text
{% include 'advanced_topics/environment_variables/stdout.txt' %}
```

The variable substitution is performed in the subshell of the worker.
StepUp will not try to substitute `${MYVAR}` before starting the step.
The special variables `${inp}` and `${out}` are exceptions to this rule,
as discussed in the [tutorial on dependencies](../getting_started/dependencies.md).

## Try the Following

- Repeat `MYVAR=foo stepup boot -n 1` without making changes.
  You will see that the `echo` step is skipped as expected.

- Now run `MYVAR=bar stepup boot -n 1`.
  This time, the variable change will cause the step to be executed.

## Injecting Environment Variables

Besides working with external environment variables,
you can also inject environment variables into the command of a step.
For example:

```python
msg = "hello"
runsh(f"MESSAGE={msg} " + "echo ${MESSAGE}")
```

Note that this is a different mechanism and it practically serves a different purpose.
In this case, there is no point in add the argument `env="MESSAGE"`,
because this step will not be sensitive to the value of `MESSAGE` defined outside StepUp.
