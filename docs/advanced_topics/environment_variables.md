# Environment Variables

When defining a step, one can specify the environment variables it uses (not their values).
When restarting StepUp with a different value for any of these variables,
StepUp will know that it has to rerun the step instead of skipping it,
even if input files have not changed.

One can only change an environment variable by stopping StepUp,
changing the variable, and then starting StepUp again.
Environment variables cannot be modified while StepUp is running.

## Example

Example source files: [`docs/advanced_topics/environment_variables/`](https://github.com/reproducible-reporting/stepup-core/tree/main/docs/advanced_topics/environment_variables)

Create the following `plan.py`:

```python
{% include 'advanced_topics/environment_variables/plan.py' %}
```

Make it executable and run StepUp with a specific value of the variable:

```bash
chmod +x plan.py
MYVAR=foo sb -j 1
```

You will see the following output:

```text
{% include 'advanced_topics/environment_variables/stdout.txt' %}
```

The variable substitution is performed in the step's execution environment.
StepUp will not try to substitute `${MYVAR}` before starting the step:
the command is sent to the director exactly as written, with no substitution
performed on it at all.
See the [tutorial on dependencies](../getting_started/dependencies.md) for how to embed
`inp`/`out`/`vol` paths into a command with `shq()`.

## Try the Following

- Repeat `MYVAR=foo sb -j 1` without making changes.
  You will see that the `echo` step is skipped as expected.

- Now run `MYVAR=bar sb -j 1`.
  This time, the variable change will cause the step to be executed.

## Injecting Environment Variables

Besides working with external environment variables,
you can also inject environment variables into the command of a step.
For example:

```python
msg = "hello"
run(f"MESSAGE={msg} " + "echo ${MESSAGE}")
```

Note that this is conceptually very different and it practically serves a different purpose.
It just sets the value of the variable before the echo command is executed.
Now, it would be incorrect to add the argument `env="MESSAGE"` to the `run()` call,
because this step will not be sensitive to the value of `MESSAGE` defined outside StepUp.

Note that this syntax for setting variables in a command is supported by StepUp
with both `shell=True` and `shell=False`, but the implementation is very different:

- With `shell=True`, the variable is set by the shell before the command is executed.
  This is completely opaque to StepUp, which just sees the command as a string.

    Because StepUp does not know which variables are set, it will not check their validity.

- With `shell=False`, a normal Python subprocess call as such would fail.
  StepUp extracts the variable assignment from the command, and stores it in the workflow database.
  When the executor runs the command, it passes the variables into the `env=...` argument
  of the subprocess call.

    In this case, StepUp will raise an error if the variable included in the command
    also appears in the `env` argument of the `run()` call, because now the mistake can be detected
    unambiguously.
