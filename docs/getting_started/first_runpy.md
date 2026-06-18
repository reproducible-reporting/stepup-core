# Running Python Scripts

When the first word of the command passed to [`run()`][stepup.core.api.run] ends in `.py`,
StepUp automatically selects a Python-aware execution mode.
It will automatically detect the modules imported by the script,
and if they correspond to local files in your workflow,
the step is amended with these files as required inputs.
This way, if the local modules have changed, StepUp will know the script needs to run again.
The script file itself is also automatically added as an input dependency.

## Example

The following example `plan.py` file shows how to run a Python script with StepUp.

```python
{% include 'getting_started/first_runpy/plan.py' %}
```

Create a `work.py` script that can be executed:

```python
{% include 'getting_started/first_runpy/work.py' %}
```

Finally, create a `helper.py` script that is imported by `work.py`:

```python
{% include 'getting_started/first_runpy/helper.py' %}
```

Make the appropriate scripts executable and run StepUp:

```bash
chmod +x plan.py work.py
stepup boot -n 1
```

You should see the following output:

```text
{% include 'getting_started/first_runpy/stdout.txt' %}
```

## Try the Following

Change the value of the variable `message` in `helper.py` and rerun StepUp with `stepup boot -n 1`.
Only `work.py` is rerun, since `plan.py` has not changed.

## Notes

- You can control which imports are treated as "local" with
  the `STEPUP_PATH_FILTER` environment variable.
  See [Environment Variables](../reference/configuration.md).

- StepUp also provides more sophisticated interfaces for running (Python) scripts,
  which are described in the following sections:

    - [Script (Single Case)](./script_single.md)
    - [Script (Multiple Cases)](./script_multiple.md)
    - [Function Calls](./call.md)

- Although not common, it is also possible to dynamically generate the Python file `helper.py`.
  In this case, the `work.py` script should only be executed after a previous step has created `helper.py`.
  For this to work, you must explicitly add the `inp` argument:

    ```python
    run("./generate.py", out=["helper.py"])
    run("./work.py", inp=["helper.py"])
    ```

    The reason for this is that the running `work.py` will fail if `helper.py` does not exist.
    By adding the `inp` option, StepUp knows not to run `work.py` until `helper.py` is available.
