# Console Scripts

New console scripts are added to a Python package
using the standard `console_scripts` entry point mechanism,
e.g. as explained in the [Python Packaging User Guide](https://packaging.python.org/en/latest/specifications/entry-points/#console-scripts).

This is the recommended approach for scripts that can also work outside of StepUp,
e.g. wrappers of external tools.
Such scripts can still call functions from `stepup.core.api`,
as these are no-ops (with some debug output) when StepUp is not running.

Install the relevant function as a `console_scripts` entry point in your package's `pyproject.toml`:

```toml
[project.scripts]
my-prog = "your.package:main_function"
```

This can be paired with a [custom API function](api.md), e.g. in `your.package.api`:

```python
from stepup.core.api import run

def my_prog(arg1, arg2):
    run(f"my-prog {arg1} {arg2}")
```

When a user calls `my_prog()` in a workflow `plan.py`,
StepUp creates a step whose command starts with `my-prog ...`.
Upon execution, StepUp recognizes `my-prog` as a Python entry point and runs it in-process,
as described under [Command Dispatch](api.md#command-dispatch).

StepUp Core uses this same pattern itself.
For example, [`render_jinja()`][stepup.core.api.render_jinja]
creates a step whose command starts with `render-jinja ...`,
which the step executor runs in-process because `render-jinja` is itself a `console_scripts`
entry point.
