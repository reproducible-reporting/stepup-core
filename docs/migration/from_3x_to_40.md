# Migration from StepUp 3.X to 4.0

A few things have changed in StepUp 4 that might require changes to your `plan.py` file.
These changes reflect optimizations of StepUp's internals or
get rid of poor historical API design choices.

What used to be called the *run phase* is now called the *build phase* in documentation and source code.
For consistency, the `stepup build` command is now the main entry point for running the build phase,
while `stepup boot` is deprecated and will be removed in a future release.
You can use the new `sb` entrypoint as a shortcut for `stepup build`.

## The new `run()` function replaces the old `runsh()` and `runpy()` functions

StepUp 4 unifies `runsh()` and `runpy()` into a single and more powerful `run()` function,
which takes an optional boolean `shell` argument (default `False`)
to indicate whether the command should be passed to a shell or not.

Roughly, the old `runsh(...)` is equivalent to `run(..., shell=True)`.
The new default, `run(..., shell=False)`, is much more general than the old `runpy(...)` function:

- It can run any executable, not just Python scripts,
  skipping the shell for better performance and reproducibility.
- It automatically detects Python scripts (ending in `.py`)
  and runs them in a forked Python interpreter.
  This is comparable to the old `runpy()` function,
  but more robust at about the same cost.
- It automatically detects so called console scripts (executables installed by Python packages)
  and runs them also in a forked Python interpreter.
  This is a new feature. In StepUp 3, such scripts were run in a shell,
  which started another Python interpreter.
  The new approach is much more efficient.

Note that the `run()` function checks whether the first word of the command
is a relative path (contains a path separator, `/`, and is not absolute).
If it is local, StepUp will automatically add it as an input dependency to the step.
In StepUp 3, one had to be explicitly include the script as an input, e.g.

```python
# StepUp 3: you had to explicitly include the script as an input
runpy("./analyze.py data.csv", inp=["analyze.py", "data.csv"])
# or
runpy("./${inp}", inp=["analyze.py", "data.csv"])

# StepUp 4: the script becomes a dependency automatically
run("./analyze.py data.csv", inp="data.csv")
# or
run("./analyze.py ${inp}", inp="data.csv")
```

### Migrating from `runsh()`

Most `runsh()` calls can be replaced with `run()` directly, without `shell=True`,
because the command is a plain executable with arguments and does not rely on shell features:

```python
# StepUp 3
runsh("./process.sh input.txt output.txt")

# StepUp 4 — no shell=True needed for plain commands
run("./process.sh input.txt output.txt")
```

Only add `shell=True` (to mimic the old `runsh()` behavior)
when the command actually requires shell interpretation,
such as pipes, redirections, globbing, or variable expansion:

```python
# StepUp 3
runsh("grep -c foo input.txt > count.txt")

# StepUp 4 — shell=True required for redirection
run("grep -c foo input.txt > count.txt", shell=True)
```

### Migrating from `runpy()`

Replace `runpy()` with `run()`.
The Python wrapper is selected automatically when the first word ends in `.py`:

```python
# StepUp 3
runpy("./analyze.py --input data.csv")

# StepUp 4
run("./analyze.py --input data.csv")
```

### Why prefer `run()` without `shell=True`

Using `shell=True` (or the old `runsh()` for plain commands) has a few drawbacks
compared to execution via `run()` with `shell=False`:

- **Reproducibility**: shell commands depend on the shell's PATH, aliases,
  and other environment state that may differ between machines or sessions.
- **Performance**: spawning a shell process adds overhead for every step.
- **Correctness**: arguments with spaces or special characters require careful quoting;
  direct execution passes arguments as-is without shell interpretation.
- **Dependency tracking**: StepUp automatically adds local relative executables
  (paths containing `/` that are not absolute) as input dependencies when using `run()`.
  This means a step is automatically re-run when its script changes.
  With `shell=True`, this tracking still applies to the first word,
  but shell-expanded paths are not tracked.

In short: use `run()` with the default `shell=False` unless you specifically need shell features.

## Directory Handling

In StepUp 3, directories were stored in the database and had to be created explicitly using `mkdir()`
or made static with `static()` or `glob()`.
In StepUp 4, directories are no longer stored in the database (except for static trees, see below).
Instead, they are created when needed.
This has a few practical consequences for your `plan.py` file:

- `mkdir()` is no longer needed and has been removed.

- When `static()` is called with a directory path, this has a different meaning than before.
  In StepUp 3, this just made the directory static.
  In StepUp 4, this makes all contained files (recursively) static.
  This implementation is *lazy*, meaning that the directory is not scanned immediately,
  but that contained files only become static when they are used as inputs.

- When `glob()` is called with a directory argument, an error is raised.

- The `_defer=True` argument to `glob()` is no longer supported.
  Use `static()` with a directory path instead, which has a similar effect.
  (Deferred globbing was slightly more flexible,
  but is now abandoned due to subtle and difficult to solve bugs.)

- Directories can no longer be used as inputs or outputs of steps.

## Distributed Plans

The function [`plan()`][stepup.core.api.plan] now works differently,
and works almost in the same way as the `run()` function,
except for a few small differences:

- The first argument is now a command string, not a directory containing another `plan.py` file.
- Except for `optional` and `shell`, all `run()` arguments are supported.
  (It is hardwired to use `optional=False, shell=False`.)
- It differs from run in that it assigns a higher priority to planning steps,
  so the workflow is completed as early as possible.
- It insists that the command is a relative path to a local executable.
  (While it would technically be possible to allow arbitrary commands,
  this easily leads to mistakes and is otherwise not useful in practice.)

In StepUp 3, you typically used the `plan()` function as follows:

```python
# StepUp 3
plan("subdir")
```

In StepUp 4, you can achieve the same effect with:

```python
# StepUp 4
plan("./plan.py", workdir="subdir")
```

The advantages of the new `plan()` function are:

- Increased flexibility: You are not forced to work in a subdirectory.
  E.g., you can have `plan_a.py` and `plan_b.py` in the same directory
  and call them both from a master `plan.py`.
- Simplicity of the API: works like a simplified version of `run()`,
  so there are fewer concepts to learn.

## Resource constraints (replacement for pools and blocked steps)

- The `pool()` function has been removed, and pools can no longer be defined in `plan.py`.
  Instead, you can declare the resources available on the host via an environment variable,
  e.g. `STEPUP_RESOURCES="gpu:2,cpu:16"` to indicate that the host has two GPUs and 16 CPU cores.
  When defining steps, you can then specify the required resources, e.g., `resources="gpu:1,cpu:4"`,
  and StepUp will ensure that the available resources are not over-committed.
  You can override the available resources with the
  `--resources` command-line argument to `stepup build` if needed.

    Note that the resource names are user-specified strings and StepUp does not implement
    pre-defined resource types, such as `gpu` or `cpu`.
    These resource definitions are only used to impose constraints when deciding which steps to run.
    You could equally use `foo` and `bar` in this example and obtain exactly the same effect.

- The `block=True` argument to `step()` and
  higher-level step-generating API functions has been removed.
  Instead, use the `resources` argument with a resource that is not available on the host,
  which will have the same effect, e.g. `resources="blocked"`.

## Changed Command-Line Arguments

The `stepup build` command was changed to have `-j` and `--jobs` options
instead of `-n` and `--num-workers`.

## Changed Environment Variable Names

The following environment variables have been renamed to have a `STEPUP_BUILD_` prefix instead of `STEPUP_`:

| Old (StepUp 3) | New (StepUp 4) |
| --- | --- |
| `STEPUP_CLEAN` | `STEPUP_BUILD_CLEAN` |
| `STEPUP_EXPLAIN_RERUN` | `STEPUP_BUILD_EXPLAIN_RERUN` |
| `STEPUP_NUM_WORKERS` | `STEPUP_BUILD_JOBS` |
| `STEPUP_PERF` | `STEPUP_BUILD_PERF` |
| `STEPUP_PROGRESS` | `STEPUP_BUILD_PROGRESS` |
| `STEPUP_SHOW_PERF` | `STEPUP_BUILD_SHOW_PERF` |
| `STEPUP_WATCH` | `STEPUP_BUILD_WATCH` |
| `STEPUP_WATCH_FIRST` | `STEPUP_BUILD_WATCH_FIRST` |
| `STEPUP_YAPPI` | `STEPUP_BUILD_YAPPI` |

## Deprecated Features

The following features are still supported but will be removed from StepUp 5.0
or a future StepUp 4.X release after June 2027, whichever comes first.
You are encouraged to migrate your `plan.py` files to the new API.

- The script interface for calling user Python scripts from `plan.py` has been deprecated
  in favor of the new [Call](../getting_started/call.md) interface.

## Optional Migration from `script()` to `call()`

The old script interface still works
(until it is removed, see [Deprecated Features](#deprecated-features) above),
but switching to [`call()`][stepup.core.api.call] is recommended.
See [Function Calls](../getting_started/call.md) for a full introduction to the new interface.

The translation is mechanical:

- Import `driver()` from `stepup.core.call` instead of `stepup.core.script`.
- Replace `script("foo.py")` in `plan.py` with `call("./foo.py", "plan", planning=True)`.
  Note the `./` prefix (the executable must be a relative path containing a separator)
  and the explicit `"plan"` function name.
- Turn the planning logic (the `info()` / `cases()` / `case_info()` functions)
  into an ordinary `plan()` function that calls `call("./foo.py", "run", ...)`
  for each run step it wants to register.
- Any `static` declared via the info dictionary becomes an explicit `static()` call.

### Single case

In StepUp 3, a single-case script returned its planning data from `info()`:

```python
# StepUp 3 — generate.py
from stepup.core.script import driver


def info():
    return {"inp": "config.json", "out": ["cos.npy", "sin.npy"]}


def run(inp, out):
    ...


if __name__ == "__main__":
    driver()
```

```python
# StepUp 3 — plan.py
from stepup.core.api import script, static

static("generate.py", "config.json")
script("generate.py")
```

In StepUp 4, the `info()` function becomes a `plan()` function that registers the run step:

```python
# StepUp 4 — generate.py
from stepup.core.api import call
from stepup.core.call import driver


def plan():
    call("./generate.py", "run", inp="config.json", out=["cos.npy", "sin.npy"])


def run(inp, out):
    ...


if __name__ == "__main__":
    driver()
```

```python
# StepUp 4 — plan.py
from stepup.core.api import call, static

static("generate.py", "config.json")
call("./generate.py", "plan", planning=True)
```

### Multiple cases

In StepUp 3, running the same script for several cases required the `cases()` generator,
a `CASE_FMT` template, and a `case_info()` function:

```python
# StepUp 3 — plot.py
from stepup.core.script import driver


def cases():
    yield "ebbr"
    yield "ebos"


CASE_FMT = "plot_{}"


def case_info(airport):
    return {
        "inp": ["matplotlibrc", f"{airport}.csv"],
        "out": f"plot_{airport}.png",
        "airport": airport,
    }


def run(inp, out, airport):
    ...
    fig.savefig(out)


if __name__ == "__main__":
    driver()
```

In StepUp 4, the same plan/run separation is kept inside the script,
but the `cases()` / `CASE_FMT` / `case_info()` machinery collapses into a plain loop
in the `plan()` function. Cases are passed as ordinary keyword arguments,
so there is no longer any `CASE_FMT`/[`parse`](https://github.com/r1chardj0n3s/parse)
string round-trip to keep consistent:

```python
# StepUp 4 — plot.py
from stepup.core.api import call
from stepup.core.call import driver


def plan():
    for airport in "ebbr", "ebos":
        call(
            "./plot.py",
            "run",
            inp=["matplotlibrc", f"{airport}.csv"],
            out=f"plot_{airport}.png",
            airport=airport,
        )


def run(inp, out, airport):
    ...
    fig.savefig(out[0])


if __name__ == "__main__":
    driver()
```

The `plan.py` file is the same as in the single-case example,
just pointing at `plot.py` instead of `generate.py`.

### Remarks

- Keeping a dedicated `plan()` function inside the script is **optional**.
  For simple cases, the loop can live directly in `plan.py`
  by calling `call("./plot.py", "run", ...)` for each case there
  (as shown in the [Call tutorial](../getting_started/call.md)).
  Conversely, a function invoked via `call()` may itself call `call()` again,
  so highly complex workflows are not limited to two stages.
  They can chain arbitrarily many levels of dynamic planning.
- In most cases, the loop in `plan()` is not the best design choice,
  as it typically hides key information about the overall workflow.
  Such loops are often better expressed in the top-level `plan.py` file.
  The fact that the old script interface imposed this anti-pattern is
  one of the reasons it was deprecated in favor of the new `call()` interface.

### Gotchas

- The first argument of `call()` must be a relative path containing a separator,
  so write `"./plot.py"`, not `"plot.py"`.
- In `run()`, the `out` argument is always a list, even when a single output path
  was passed to `call()`. Use `out[0]` where the old `run()` could use `out` directly.
- Replace `script(..., optional=True)` with `call(..., optional=True)`;
  the value is forwarded to the run steps automatically.
- The `step_info=...` argument of `script()` is no longer needed:
  because `plan()` registers the run steps directly, their information is available
  without writing an intermediate JSON file.

## Abandoned Features

The following were practically unused and have been removed:

- The `_required=True` argument to `glob()`.
  In the rare cases that it is useful, it can be implemented with a simple check in the `plan.py` file.
- The previously experimental `call()` API has been replaced by an incompatible new design.
  No migration path is needed given its experimental status and limited adoption;
  see [Function Calls](../getting_started/call.md) for the new interface.

## Changes for Extension Package Developers

If you are developing a StepUp extension package (i.e., you import from `stepup.core`
to build custom API functions or tools),
the following utilities have moved to the new
[`stepup.core.extapi`](../reference/stepup.core.extapi.md) module:

| Function | Old location | New location |
| --- | --- | --- |
| `subs_env_vars` | `stepup.core.api` | `stepup.core.extapi` |
| `get_rpc_client` | `stepup.core.api` | `stepup.core.extapi` |
| `filter_dependencies` | `stepup.core.utils` | `stepup.core.extapi` |
| `get_local_import_paths` | `stepup.core.utils` | `stepup.core.extapi` |

Update your imports accordingly:

```python
# StepUp 3 / early StepUp 4
from stepup.core.api import get_rpc_client, subs_env_vars
from stepup.core.utils import filter_dependencies, get_local_import_paths

# StepUp 4 (current)
from stepup.core.extapi import filter_dependencies, get_local_import_paths, get_rpc_client, subs_env_vars
```
