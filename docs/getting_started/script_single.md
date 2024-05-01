# Script (Single Case)

StepUp Core implements a simple *script protocol* for defining scripts that combine planning and execution in a single source file.
This can be more convenient than putting a lot of detail in the `plan.py` file.


## Script Protocol

The [`script()`][stepup.core.api.script] protocol itself is rather simple.
The following line in `plan.py`:

```python
script("sub/executable")
```

is roughly equivalent to:

```python
step("./executable plan", inp="sub/executable", workdir="sub/")
```

where the subdirectory is optional.
The step `./executable plan` is expected to define additional steps to actually run something useful with the executable.
A common scenario is that it plans a single step `./executable run` with appropriate inputs and outputs.


## Script driver

StepUp implements a `driver` function in the module `stepup.core.script` that greatly facilitates
writing Python scripts that adhere to the script protocol.

It can be used in two ways:

1. To run the executable for just one specific case of inputs and outputs (this tutorial).

2. To run the same script with multiple combinations of inputs and outputs ([next tutorial](script_multiple.md)).


## Single Case Script Driver

A Python script using the driver for a single case has the following structure.


```python
#!/usr/bin/env python
from stepup.core.script import driver

def info():
    return {
        "inp": ..., # a single input path or a list of input paths
        "out": ..., # a single output path or a list of input paths
        "just_any": "argument that you want to add",
    }

def run(inp, out, just_any):
    ...

if __name__ == "__main__":
    driver()
```

- The `info` function provides the necessary data to implement
  the planning of the execution of the script.
  It is executed when calling the script as `./script.py plan`.

- The `run` function is called to perform the useful work and
  is executed when running the script with `./script.py run`.

Note that the `run` function can have any argument defined in the dictionary return by `info`,
but it does not have to specify all of them.
The argument list of `run` may also contain fewer arguments (or even none at all).


## Example

Example source files: [getting_started/script_single/](https://github.com/reproducible-reporting/stepup-core/tree/main/docs/getting_started/script_single)

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
stepup -n -w1
```

You should see the following output on screen:

```
{% include 'getting_started/script_single/stdout.txt' %}
```

As expected, this creates two files: `cos.npy` and `sin.npy`.


## Try the Following:

- Modify the file `config.json` and re-run StepUp.
  The planning is skipped because the script itself did not change.
  Only the run function is called to work with the updated `config.json`.

- Delete one of the outputs and rerun StepUp.
  Again, the planning is skipped and the computation is repeated to recreate the missing output.

- Create a new module `utils.py` with a `compute` function to calculate the cosine and sine arrays
  with parameters `nstep` and `freq`.
  Import this module into `generate.py`, use it in `run` and re-run StepUp.
  This will automatically make `utils.py` an input for the planning and running of `generate.py`.
  Test this by making a small change to `utils.py` and re-running it.
  (Note that local imports inside the `run` function will not be identified automatically and
  are therefore not recommended.)
