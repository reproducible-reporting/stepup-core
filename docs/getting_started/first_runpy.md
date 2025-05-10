# Running Python scripts

In principle, you can incorporate any Python script into a StepUp workflow
with the [`runsh()`][stepup.core.api.runsh] function.
However, it is recommended to use the [`runpy()`][stepup.core.api.runpy] function instead.
It will automatically detect modules imported by the script,
and if these correspond to local files in your workflow,
the files are amended as required inputs of the step.
This way, when the local modules have changed, StepUp knows the script must be re-executed.

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

Make the relevant scripts executable and run StepUp:

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
Only `work.py` will be re-executed, because `plan.py` has not changed.

## Notes

- You can control which imports are treated as "local" with the environment variable
  `STEPUP_EXTERNAL_SOURCES`. See [Environment Variables](../reference/environment_variables.md).

- StepUp also provides more elaborate interface for running (Python) scripts,
  which are described in the following sections:

    - [Script (single)](./script_single.md)
    - [Script (multiple)](./script_multiple.md)
    - [Function calls](./call.md)

- While uncommon, it is also possible to generate the Python file `helper.py` dynamically.
  In this case, the script `work.py` should only be executed when a previous step has created `helper.py`.
  To make this work, you must add the `inp` argument explicitly:

    ```python
    runpy("./generate.py > ${out}", out=["helper.py"])
    runpy("./work.py", inp=["helper.py"])
    ```

    The reason is that the execution of `work.py` will fail when `helper.py` is not present.
    By adding the `inp` option, StepUp knows that it should only execute `work.py`
    after `helper.py` has become available.
