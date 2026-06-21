# Command Dispatch

When a step executes, StepUp's executor automatically selects how to run the command.
The dispatch logic depends on the `shell` flag passed to [`step()`][stepup.core.api.step]
and the form of the command string:

- **Shell mode** (`shell=True`):
  The full command string is passed to a shell.
  Shell features such as pipes and redirections are available.

- **Python script** (`shell=False` and the first word ends in `.py`):
  The script is executed via a Python wrapper that auto-detects local imports
  and amends them as step inputs.

- **Python entry point** (`shell=False`, no slashes in the first word,
  and the command matches a `console_scripts` entry point in the current Python environment):
  The entry point function is called in-process via the forkserver when available,
  avoiding the overhead of spawning a new Python interpreter.
  If the executable belongs to a different Python environment, a warning is printed
  and execution falls back to a direct execution.

- **Direct execution** (all other cases):
  The command is executed directly without a shell.
  This is the fastest and safest mode for non-Python commands.

## Leveraging the Forkserver in Extensions

If your extension provides steps that run Python code and you want to avoid per-step
interpreter startup overhead, install the relevant function as a `console_scripts` entry
point in `pyproject.toml`:

```toml
[project.scripts]
my-tool = "your.package:main_function"
```

When a plan script calls:

```python
run("my-tool arg1 arg2")
```

StepUp detects `my-tool` as a Python entry point and calls `main_function` in-process
via the forkserver, with no new Python interpreter launched.

This is the same pattern used in StepUp Core itself.
For example, `render_jinja()` in `stepup.core.api` creates a step whose command starts with
`stepup render-jinja ...`.
Because `stepup` is a `console_scripts` entry point, the worker runs it in-process
via the forkserver instead of spawning a new process.
