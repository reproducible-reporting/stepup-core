# Variable Substitution

StepUp does not substitute environment variables is the command (first argument) of the [`step()`][stepup.core.api.step] function.
As discussed in the tutorial on [environment variables](environment_variables.md), the executing shell takes care of such substitutions.

However, environment variables in all path-like arguments (e.g. `workdir`, `inp`, `out` and `vol`) of functions that take such arguments ([`step()`][stepup.core.api.step], [`amend()`][stepup.core.api.amend] etc.) are automatically substituted.
This substitution takes place before the commands are sent to the director process and all used variables are communicated to the director with an `amend()` call.

If a script needs an environment variable elsewhere, the function [`getenv()`][stepup.core.api.getenv] is recommended:
It returns the value of the variable and calls `amend()` to tell the director that the current step depends on this variable.


## Example

Example source files: [advanced_topics/variable_substitution/](https://github.com/reproducible-reporting/stepup-core/tree/main/docs/advanced_topics/variable_substitution)

Create a `plan.py` with the following contents:

```python
{% include 'advanced_topics/variable_substitution/plan.py' %}
```

In addition, create a script `step.py` as follows:

```python
{% include 'advanced_topics/variable_substitution/step.py' %}
```

Make the Python scripts executable and run them as follows:

```bash
chmod +x plan.py step.py
MYVAR=foo stepup -n -w1
```

You should get the following terminal output:

```
{% include 'advanced_topics/variable_substitution/stdout.txt' %}
```

The file `dst_foo.txt` will contain the following:

```
{% include 'advanced_topics/variable_substitution/dst_foo.txt' %}
```

As shown in this example, the function [`getenv()`][stepup.core.api.getenv] returns `None` when a variable does not exist (or any other default you specify).
When using variables like `${MYVAR}` in path-like arguments, the variable must exist or an exception is raised.


## Try the Following

- Run StepUp without defining `MYVAR`: `stepup -n -w1`.
  As explained above, this raises an exception.
- Run StepUp by also defining `MYNUM`: `MYVAR=foo MYNUM=1 stepup -n -w1`.
  Now the string `'1'` is shown in the output `dst_foo.txt`.
  Note that environment variables are always strings, and need to be converted to other types if needed.
