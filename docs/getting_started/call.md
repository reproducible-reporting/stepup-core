# Function calls

!!! note

    This feature was introduced in StepUp 2.0.0.

!!! warning

    This feature is experimental and may change significantly in future 2.x releases.

The [`call()`][stepup.core.api.call] function is a function wrapper for local executable scripts.
These scripts can be written in any language,
as long as they adhere to the "call protocol" described below.
StepUp provides a `driver()` function in the module `stepup.core.call`
to facilitate the implementation of Python scripts that adhere to the call protocol


## Call protocol

In its simplest form, the following `call()` in a `plan.py` script

```python
call("executable", parameter="value")
```

is roughly equivalent to

```python
step("./executable '{\"parameter\": \"value\"}'", inp="executable")
```

(It also works without any parameters.)
The script `executable` is expected to decode the JSON given on the command-line,
and then use these parameters.

The [`call()`][stepup.core.api.call] function supports many optional arguments
that control with which options the executable is called.
In a nutshell, the parameters can also be provided through an input file.
By default, this is a JSON file, but PICKLE is provided as a fallback
in case the types of the parameters are not compatible with JSON.
When the executable script produces a "return value",
it should write this to an output file, either in JSON or PICKLE format.

Because of the delayed execution, the `call()` function cannot return this value.
If you are familiar with Python's builtin `concurrent.futures` module,
you can think of the output file of the script as the `Future` object that is returned by `concurrent.futures.Executor.submit()`.
The `call()` function returns a [`StepInfo`][stepup.core.stepinfo.StepInfo] object from which you can extract the output file path.

To fully support the `call()` protocol, the executable must be able to handle the following options:

- `JSON_INP`: The JSON-encoded parameters passed on the command-line
- `--inp=PATH_INP`: As an alternative to the previous, a file with parameters in JSON or PICKLE format.
- `--out=PATH_OUT`: The output file to use (if there is a return value), either in JSON or PICKLE format.
- `--amend-out`: If given, the executable must call `amend(out=PATH_OUT)` before writing the output file.


## Call driver

StepUp implements a `driver()` function in the module `stepup.core.call` that greatly facilitates
writing Python scripts that adhere to the call protocol.
The usage of this `driver()` function is illustrated in the example below.

Note that the `driver()` function also detects local modules that are imported in the script,
and amends these as required inputs.
Changes to modules imported in your Python script will automatically trigger a re-run of the script.


## Example

Example source files: [`docs/getting_started/call/`](https://github.com/reproducible-reporting/stepup-core/tree/main/docs/getting_started/call)

First create a `wavegen.py` script as follows:

```python
{% include 'getting_started/call/wavegen.py' %}
```

Then create a `plan.py` script as follows:

```python
{% include 'getting_started/call/plan.py' %}
```

Finally, make the Python scripts executable and start StepUp:

```bash
chmod +x wavegen.py plan.py
stepup -n 1
```

You should see the following output on screen:

```
{% include 'getting_started/call/stdout.txt' %}
```


## Practical considerations

- As shown by the example, the `driver()` function takes care of
  parsing the command-line arguments in the call protocol and amending the output.
  You don't need to worry about this in your script.
  You simply add the `run()` function that takes the parameters as arguments
  and returns the result.
- Scripts that use the `driver()` function can be run as standalone scripts.
  This is useful for debugging and testing.
- Scripts that adhere to the call protocol can be reused across the entire workflow.
  If you want to place it in the top-level directory and execute it in any other directory, you can use the [`ROOT`](../advanced_topics/here_and_root.md) variable:

    ```python
    call("${ROOT}/wavegen.py", freq=235, duration=1.0, out="sine.json")
    ```
