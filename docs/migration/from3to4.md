# Migration from StepUp 3.X to 4.X

A few things have changed in StepUp 4 that might require changes to your `plan.py` file.
These changes reflect optimizations of StepUp's internals or
get rid of poor historical API design choices.

## The new `run()` function replaces the old `runsh()` and `runpy()` functions

StepUp 4 unifies `runsh()` and `runpy()` into a single `run()` function.
The action (shell, Python, or direct execution) is now selected automatically based on the command:

| Condition | Action | Equivalent old function |
| --- | --- | --- |
| `shell=True` | `runsh` — passed to a shell | `runsh()` |
| First word ends in `.py` | `runpy` — Python wrapper with local import detection | `runpy()` |
| Otherwise | `runexec` — direct execution, no shell | `runsh()` (previously had no `runexec` equivalent) |

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
run("./analyze.py data.csv", inp=["data.csv"])
# or
run("./analyze.py ${inp}", inp=["data.csv"])
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
compared to direct execution via `runexec`:

- **Reproducibility**: shell commands depend on the shell's PATH, aliases,
  and other environment state that may differ between machines or sessions.
- **Performance**: spawning a shell process adds overhead for every step.
- **Correctness**: arguments with spaces or special characters require careful quoting;
  direct execution passes arguments as-is without shell interpretation.
- **Dependency tracking**: StepUp automatically adds local relative executables
  (paths containing `/` that are not absolute) as input dependencies when using `runexec`.
  This means a step is automatically re-run when its script changes.
  With `shell=True`, this tracking still applies to the first word,
  but shell-expanded paths are not tracked.

In short: use `run()` without `shell=True` unless you specifically need shell features.

## Directory Handling

In StepUp 3, directories were stored in the database and had to be created explicitly using `mkdir()`
or made static with `static()` or `glob()`.
In StepUp 4, directories are no longer stored in the database (except for static roots, see below).
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

## Resource constraints (replacement for pools and blocked steps)

- The `pool()` function has been removed, and pools can no longer be defined in `plan.py`.
  Instead, you can declare the resources available on the host via an environment variable,
  e.g. `STEPUP_RESOURCES="gpu:2,cpu:16"` to indicate that the host has two GPUs and 16 CPU cores.
  When defining steps, you can then specify the required resources, e.g., `resources="gpu:1,cpu:4"`,
  and StepUp will ensure that the available resources are not over-committed.
  You can override the available resources with the
  `--resources` command-line argument to `stepup boot` if needed.

    Note that the resource names are user-specified strings and StepUp does not implement
    pre-defined resource types, such as `gpu` or `cpu`.
    These resource definitions are only used to impose constraints when deciding which steps to run.
    You could equally use `foo` and `bar` in this example and obtain exactly the same effect.

- The `block=True` argument to `step()` and
  higher-level step-generating API functions has been removed.
  Instead, use the `resources` argument with a resource that is not available on the host,
  which will have the same effect, e.g. `resources="blocked"`.

## Changed Environment Variable Names

The following environment variables have been renamed to have a `STEPUP_BOOT_` prefix instead of `STEPUP_`:

| Old (StepUp 3) | New (StepUp 4) |
| --- | --- |
| `STEPUP_CLEAN` | `STEPUP_BOOT_CLEAN` |
| `STEPUP_EXPLAIN_RERUN` | `STEPUP_BOOT_EXPLAIN_RERUN` |
| `STEPUP_NUM_WORKERS` | `STEPUP_BOOT_NUM_WORKERS` |
| `STEPUP_PERF` | `STEPUP_BOOT_PERF` |
| `STEPUP_PROGRESS` | `STEPUP_BOOT_PROGRESS` |
| `STEPUP_SHOW_PERF` | `STEPUP_BOOT_SHOW_PERF` |
| `STEPUP_WATCH` | `STEPUP_BOOT_WATCH` |
| `STEPUP_WATCH_FIRST` | `STEPUP_BOOT_WATCH_FIRST` |
| `STEPUP_YAPPI` | `STEPUP_BOOT_YAPPI` |

## Abandoned Features

The following were practically unused and have been removed:

- The `_required=True` argument to `glob()`.
  In the rare cases that it is useful, it can be implemented with a simple check in the `plan.py` file.
