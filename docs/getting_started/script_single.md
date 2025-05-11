# Script (Single Case)

StepUp Core implements a simple *script protocol*
for defining scripts that combine planning and execution in a single source file.
This can be more convenient than putting a lot of detail in the `plan.py` file.

The script and [call](call.md) protocols are similar in that they both simplify planning step.
To help you decide which one to use, consider the following:

- The **call protocol** is most convenient when you have a single script
  that can be run with different parameters.
  The script itself does not need to know about the parameters upfront.

- The **script protocol** is most convenient when you prefer to have all the details of the script,
  including the planning (inputs, outputs, ...) and execution, in a single source file.
  Such self-contained scripts can be easier to understand and maintain.

If both seem overkill, you can also use the [`runpy()`][stepup.core.api.runpy] function directly in `plan.py`.
See [Running Python Scripts](first_runpy.md) for an example.

## Script Protocol

The [`script()`][stepup.core.api.script] protocol itself is rather simple.
The following line in `plan.py`:

```python
script("executable", workdir="sub")
```

is roughly equivalent to:

```python
runsh("./executable plan", inp="executable", workdir="sub")
```

Note that the use of a subdirectory is not required.
The `./executable plan` step is expected to define additional steps
to actually do something useful with the executable.
A common scenario is to plan a single `./executable run` step with appropriate inputs and outputs.
Note that if the script has the `.py` extension, `runpy()` is used instead of `runsh()`.

When the `optional=True` keyword argument is given to the `script()` function,
it executes `./executable plan --optional`.
The script protocol requires that all *run* steps created by this planning step
should then receive the `optional=True` keyword argument.
Note that the *plan* step itself is never an optional step:
It is always executed.

The `script()` function also supports the `step_info="some_file.json` argument.
This results in an extra argument `--step-info=some_file.json` for `./executable plan`.
The planning of the run scripts is then expected to
write all the information about the created run steps to this JSON file.
In complex workflows, it may be useful to load this JSON file with
[`load_step_info()`][stepup.core.stepinfo.load_step_info].
Consult the section on [StepInfo Objects](../advanced_topics/step_info.md)
to learn how to use these objects.

In addition, the `script()` function also supports all the arguments of `step()`,
which it simply passes on when creating the `./executable plan` step.

!!! note

    The `step_info` argument, and support for all `step()` arguments were added in StepUp 2.0.0.

## Script driver

StepUp implements a [`driver()`][stepup.core.script.driver] function
in the module `stepup.core.script` that greatly facilitates
writing Python scripts that adhere to the script protocol.

It can be used in two ways:

1. To run the executable for just one specific case of inputs and outputs (this tutorial).

2. To run the same script with multiple combinations of inputs and outputs ([next tutorial](script_multiple.md)).

In both cases, the script driver will detect local modules that are imported by the script,
and amend the script step with the loaded modules as required inputs.
By default, only the modules inside `${STEPUP_ROOT}`
(but not in `${STEPUP_ROOT}/venv*`) are treated as dependencies.
You can control the filtering of automatically detected dependencies with the
[`STEPUP_PATH_FILTER` environment variable](../reference/environment_variables.md).

## Single Case Script Driver

A Python script using the driver for a single case has the following structure.

```python
#!/usr/bin/env python3
from stepup.core.script import driver

def info():
    return {
        "inp": ..., # a single input path or a list of input paths
        "out": ..., # a single output path or a list of input paths
        "static": ..., # declare a static file or a list of static files
        "just_any": "argument that you want to add",
    }

def run(inp, out, just_any):
    ...

if __name__ == "__main__":
    driver()
```

- The `info()` function provides the data necessary to plan the execution of the script.
  It is executed when calling the script as `./script.py plan`.

    !!! note

        All dictionary items are optional.
        The `info()` function can even return an empty dictionary.
        The keys `inp`, `out` and `static` affect the planning the run part,
        as explained in the comments above.

- The `run()` function is called to perform the useful work and
  is executed when the script is executed as `./script.py run`.

    !!! note

        The `run()` function can have any argument defined in the dictionary returned by `info()`,
        but it does not have to specify all of them.
        The argument list of `run()` can contain fewer arguments (or even none at all).

## Example

Example source files: [`docs/getting_started/script_single/`](https://github.com/reproducible-reporting/stepup-core/tree/main/docs/getting_started/script_single)

Consider a script that has parameters defined in a config file `config.json`,
which may be used by multiple script, e.g. for reasons of consistency.
For this example, the configuration contains a number of steps and a frequency in arbitrary units,
serialized in a JSON file:

```json
{% include 'getting_started/script_single/config.json' %}
```

This file is used in a script `generate.py` as follows:

```python
{% include 'getting_started/script_single/generate.py' %}
```

Also, add the following `plan.py` file:

```python
{% include 'getting_started/script_single/plan.py' %}
```

Finally, make the Python scripts executable and give StepUp a spin:

```bash
chmod +x generate.py plan.py
stepup boot -n 1
```

You should see the following output on screen:

```text
{% include 'getting_started/script_single/stdout.txt' %}
```

As expected, this creates two files: `cos.npy` and `sin.npy`.

## Try the Following

- Modify the file `config.json` and re-run StepUp.
  The planning is skipped because the script itself did not change.
  Only the run function is called to work with the updated `config.json`.

- Delete one of the outputs and rerun StepUp.
  Again, the planning is skipped and the computation is repeated to recreate the missing output.

- Create a new module `utils.py` with a `compute()` function to calculate the cosine and sine arrays
  with parameters `nstep` and `freq`.
  Import this module into `generate.py`, use it in `run()` and re-run StepUp.
  This will automatically make `utils.py` an input for the planning and running of `generate.py`.
  Test this by making a small change to `utils.py` and re-running it.
  (Note that local imports inside the `run()` function will not be identified automatically and
  are therefore not recommended.)
