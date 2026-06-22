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
stepup boot -j 1
```

You should see the following output:

```text
{% include 'getting_started/first_runpy/stdout.txt' %}
```

## Try the Following

Change the value of the variable `message` in `helper.py` and rerun StepUp with `stepup boot -j 1`.
Only `work.py` is rerun, since `plan.py` has not changed.

## Running Python Entry Points

Many Python tools are installed as command-line programs and are invoked by bare
name rather than by a `.py` file path.
Some examples (in the context of document processing) include `pymupdf` and `weasyprint`.
These are Python programs that you can just run from the terminal.

When these are installed in the same Python environment as StepUp and used in a `run()` command,
StepUp automatically detects that they are `console_scripts` entry points
(i.e. command-line programs installed by Python packages).
Consider the following example:

```python
run("weasyprint ${inp} ${out}", inp="document.html", out="document.pdf")
```

StepUp will create a `runpyep` action for this command,
which is similar to `runpy` but designed to run Python entry points.
If the `--fork-runpy` option is enabled (the default on Linux),
StepUp will run the command with the forkserver mechanism,
which is significantly faster than a normal subprocess.

Several sanity checks are performed to ensure that the command
is a proper Python entry point from the same Python environment as StepUp.
If not, StepUp will fall back to running the command with the `runexec` action,
which always executes the command as a normal subprocess.

## Notes

- You can control which imports are treated as "local" with
  the `STEPUP_PATH_FILTER` environment variable.
  See [Environment Variables](../reference/configuration.md).

- StepUp also provides a more sophisticated interface for running functions in (Python) scripts,
  which is described in the [Function Calls](./call.md) tutorial.

- When `--fork-runpy` is active (the default on Linux), you can pre-load additional modules
  into the forkserver with `--preload-modules` (or `preload_modules` in the config file).
  This further reduces per-step startup time for workflows that repeatedly import the same
  large packages such as NumPy or Matplotlib.
  See [Configuration](../reference/configuration.md) for details.

- Although not common, it is also possible to dynamically generate the Python file `helper.py`.
  In this case, the `work.py` script should only be executed after a previous step has created `helper.py`.
  For this to work, you must explicitly add the `inp` argument:

    ```python
    run("./generate.py", out="helper.py")
    run("./work.py", inp="helper.py")
    ```

    The reason for this is that the running `work.py` will fail if `helper.py` does not exist.
    By adding the `inp` option, StepUp knows not to run `work.py` until `helper.py` is available.
