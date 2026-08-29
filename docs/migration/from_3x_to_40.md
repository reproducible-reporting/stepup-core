---
description: >-
  Update your plan.py for StepUp 4, in which run() replaces runsh() and runpy(),
  stepup build replaces stepup boot, and the graph database format changed.
---

# Migration from StepUp 3.X to 4.0

<!--
SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
SPDX-License-Identifier: CC-BY-SA-4.0
-->

StepUp 4 comes with many new features and improvements,
some of which required backward incompatible changes.
As a result, you may need to make some changes to your `plan.py` file
when upgrading from StepUp 3 to 4.
Also the database format (used in `.stepup/graph.db`) has changed.
If you have an existing StepUp 3 database, it will be ignored
and your entire workflow will be re-executed to recreate the database in the new format.

What used to be called the *run phase* is now called the *build phase* in documentation and source code.
For consistency, the `stepup build` command is now the main entry point for running the build phase,
while `stepup boot` is deprecated and will be removed in a future release.
You can use the new `sb` entrypoint as a shortcut for `stepup build`.
The `stepup run` subcommand, which starts a new build phase in a running StepUp instance,
is renamed to `stepup rebuild` for the same reason.

## The New `run()` Function Replaces the Old `runsh()` and `runpy()` Functions

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
- It automatically detects so-called console scripts (executables installed by Python packages)
  and runs them in a forked Python interpreter.
  This is a new feature. In StepUp 3, such scripts were run in a shell,
  which started another Python interpreter.
  The new approach is much more efficient.

Note that the `run()` function checks whether the first word of the command
is a relative path (contains a path separator, `/`, and is not absolute).
If it is local, StepUp will automatically add it as an input dependency to the step.
In StepUp 3, one had to explicitly include the script as an input.

StepUp 4 drops the magic substitution of `${inp}` and `${out}` in the command string.
Instead, you can use Python's f-string syntax and the `shq()` function to insert paths in the command.
This is a more general and robust approach than the old substitution mechanism.
The `shq()` function handles single paths or lists of paths and adds quotes where needed,
and gives you more control over which paths to insert where, e.g. using slicing.
In simple cases, just repeating the input and output paths in the command string
is recommended for readability.

```python
# StepUp 3: you had to explicitly include the script as an input
runpy("./analyze.py data.csv", inp=["analyze.py", "data.csv"])
# or
runpy("./${inp}", inp=["analyze.py", "data.csv"])

# StepUp 4: the script becomes a dependency automatically
run("./analyze.py data.csv", inp="data.csv")
# or, if the input path is stored in a variable:
inp = "data.csv"
run(f"./analyze.py {shq(inp)}", inp=inp)
```

### Migrating from `runsh()`

Most `runsh()` calls can be replaced with `run()` directly, without `shell=True`,
because the command is a plain executable with arguments and does not rely on shell features:

```python
# StepUp 3
runsh("./process.sh input.txt output.txt", inp=["process.sh", "input.txt"], out="output.txt")

# StepUp 4: no shell=True needed for plain commands
run("./process.sh input.txt output.txt", inp="input.txt", out="output.txt")
```

Only add `shell=True` (to mimic the old `runsh()` behavior)
when the command actually requires shell interpretation,
such as pipes, redirections, globbing, or variable expansion:

```python
# StepUp 3
runsh("grep -c foo input.txt > count.txt", inp="input.txt", out="count.txt")

# StepUp 4: shell=True required for redirection
run("grep -c foo input.txt > count.txt", shell=True, inp="input.txt", out="count.txt")
```

### Migrating from `runpy()`

Replace `runpy()` with `run()`.
The Python wrapper is selected automatically when the first word ends in `.py`:

```python
# StepUp 3
runpy("./analyze.py --input data.csv", inp=["analyze.py", "data.csv"])

# StepUp 4
run("./analyze.py --input data.csv", inp="data.csv")
```

### Why Prefer `run()` without `shell=True`

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

## `static()` and `glob()` Have New Roles

In StepUp 3, [`static()`][stepup.core.api.static] and [`glob()`][stepup.core.api.glob]
differed by *how you named the files*:
`static()` took literal paths, `glob()` took patterns,
and both declared their files static.
In StepUp 4, they differ by *what they do*.
`static()` declares, with or without wildcards.
`glob()` only looks: it is a pure query that owns nothing it matches.
(Matches must be declared static elsewhere.)
The following table compares the new and old roles:

| Role | StepUp 3 | StepUp 4 |
| --- | --- | --- |
| Declare a static file | `static("data/foo.txt")` | `static("data/foo.txt")` |
| Declare static files by pattern | `glob("*.txt")` | `static("*.txt")` |
| Query files by pattern | *N.A.* | `glob("*.txt")` |

### Migration Of StepUp 3 `glob()` calls

Every loop that used `glob()` with *conventional wildcards* to declare *files* (not directories)
becomes a call to `static()`:

```python
# StepUp 3
for path in glob("src/*.txt"):
    copy(path, "out/" + path.name)

# StepUp 4
for path in static("src/*.txt"):
    copy(path, "out/" + path.name)
```

An unmigrated plan fails loudly:
the matched files are never declared static,
so the steps that use them are stuck with unavailable inputs.
There is no silent misbehaviour to watch out for here.

There are a few corner cases that require a more careful migration:

- If the `glob()` call matched directories, keep using `glob()`
   because StepUp 4 no longer keeps track of directories in the database.
  To make this safe, the matched directories must contain a static file,
  static tree, or sit inside a static tree.
- If the `glob()` call used the `_defer=True` argument,
  use a [static tree](../getting_started/static_tree.md) instead, e.g. `static("src/")`
  and query the files with `glob()` when needed.
- If the `glob()` call used named wildcards (`${*name}`)
  and the captured substrings were used in the loop body,
  keep using `glob()` and declare the matches as static files where needed.
  See [Named Glob](../getting_started/named_glob.md) for details.

### `static()` Takes Patterns and Returns Paths

The new `static()` gained the two properties that made `glob()` convenient:

- It accepts glob patterns with anonymous (`*`, `?`, `[abc]`) and named (`${*name}`)
  wildcards, in addition to literal paths.
  A file match is declared static, a directory match is registered as a static tree,
  and a pattern without matches is not an error.
- It returns a sorted list of the paths it covers,
  so its result can be iterated directly.

As with `glob()`, the pattern is registered with the calling step,
so the step becomes pending and re-runs when the set of matches changes.
See [Glob Patterns in `static()`](../getting_started/static_patterns.md).

### The Escaping Gotcha

Because every argument of `static()` is now read as a pattern,
the characters `*`, `?` and `[` are significant where they used to be literal.
This is the one change in this section that can break a working plan
without an error message pointing at the cause:
a file named `table[1].csv` is no longer declared by spelling out its name,
because the argument is interpreted as a character class.

Wrap such paths in
[`glob.escape()`](https://docs.python.org/3/library/glob.html#glob.escape)
from the standard library:

```python
import glob as globmod

static(globmod.escape("table[1].csv"))
```

### A Trailing `**` Is Rejected by `static()`

A recursive `**` wildcard as the *final* path component is not accepted by `static()`,
e.g. `static("src/**")` or `static("**")`.
Declare the directory as a [static tree](../getting_started/static_tree.md) instead,
e.g. `static("src/")`, which covers the whole subtree lazily.
A `**` earlier in the pattern, e.g. `static("src/**/*.c")`, is accepted.
Use `glob()` when a recursive *list* of files is genuinely needed.

### Overlapping Patterns Are Now Allowed

This is a new capability rather than a migration,
but it removes workarounds that StepUp 3 forced upon you.

- A single plan may declare the same file twice,
  e.g. with `static("*/*.tex")` and `static("figures/*.*")`.
  A repeated declaration by the same step is a silent no-op.
- Because a query owns nothing, any number of `glob()` calls may match the same file,
  from any number of plans.
  The ordering tricks and artificially non-overlapping patterns that StepUp 3 required
  can all be deleted.

### The End-of-Build Check

A `glob()` match that no `static()` declaration and no static tree justifies
cannot be judged while the build is running,
since the plan that would declare it may not have run yet.
It is checked once at the end of the build phase and reported as a warning:

```text
N glob match(es) are not declared static.
```

This is a warning and nothing more: it does not fail the build.
It only sets the warning bit of the return code
(see [Return Codes](#return-codes-have-been-renumbered) below).
It has one fatal sibling, reported as an error and setting the `FAILED` return-code bit:

```text
N glob match(es) are files that a step builds.
```

A glob pattern may only match static files,
so a match that some step builds is always a mistake.
See [Glob](../getting_started/glob.md) for both checks in context.

### Named Globs

`static()` accepts named wildcards (`${*name}`)
and honours the back-reference constraint,
so `static("ch${*ch}/sec${*ch}_*.txt")` is valid.
It has no `subs` keyword arguments, though,
so a named wildcard there always uses the default sub-pattern `*`,
and the captured substrings are not part of the return value.
Use the composition `static(glob(pattern, **subs))` when you need either;
`static()` accepts the `NamedGlob` object returned by `glob()` directly
and does not register the pattern a second time.
See [Named Glob](../getting_started/named_glob.md).

## Directory Handling

In StepUp 3, directories were stored in the database
and had to be created explicitly using `mkdir()` or made static with `static()` or `glob()`.
In StepUp 4, directories are no longer stored in the database (except for static trees, see below).
Instead, they are created automatically when needed.
This has a few practical consequences for your `plan.py` file:

- `mkdir()` is no longer needed and has been removed.

- Directories can no longer be used as inputs or outputs of steps.

- The `_defer=True` argument to `glob()` is no longer supported.
  Use `static()` with a directory path instead, which has a similar effect.
  (Deferred globbing was slightly more flexible,
  but is now abandoned due to subtle and difficult to solve bugs.)

### Static Trees Instead of Static Directories

Passing a directory to `static()` has a different meaning than before.
In StepUp 3, this just made the directory itself static.
In StepUp 4, it registers a *static tree*, which makes all contained files (recursively) static.
This implementation is *lazy*, meaning that the directory is not scanned immediately,
but that contained files only become static when they are used as inputs.

Three rules govern how static trees interact with `static()` and `glob()`:

1. **A static tree is the sole owner of the files under it.**

    A *single plan* may declare `static("data/")` and `static("data/foo.txt")` in either
    order:

    - If the tree is declared first, the static file declaration is a no-op.
      (It will become static when it is first used as an input.)
    - If the file is declared first, the tree takes ownership of it.
      (If the static file appears unused at the end of a successful build,
      it is removed from the database.)

    The build graph in the end is the same in both cases.

    Two *different plans* doing the same thing
    (one declaring the tree, the other the file inside it)
    raise an error, again in either order, since only one of them can own the file.
    Use `glob()` to list the files inside another plan's static tree without claiming them.

2. **`glob()` no longer declares anything**,
   so overlapping `glob()` calls are allowed.
   See [`static()` and `glob()` Have New Roles](#static-and-glob-have-new-roles)
   above for the migration this requires.

3. **A directory match is treated differently by the two functions.**
   A `static()` pattern that matches a directory registers a static tree for it,
   just like a directory listed by name.
   A `glob()` pattern may match a directory anywhere, but the match must be justified
   by the end of the build phase: it must lie inside a static tree, contain one,
   or contain a static file.
   An unjustified match is reported as a warning, not an error.

### Trailing Slashes

StepUp 3 strongly insisted on trailing slashes for directory paths.
This requirement has been abandoned almost entirely in StepUp 4.
End users only need to specify such "path affixes" in two places to avoid ambiguity:

- If the `dst` argument of `copy()` is a directory, it must end with a trailing slash.
  (StepUp cannot check the file system to test if it is a directory
  because the directory may not exist yet.)
- When specifying a local executable, it must either start with a `./` prefix
  or be a relative path containing a path separator (`/`).
  This is needed to avoid ambiguity with executables found in the PATH.

## Distributed Plans

The function [`plan()`][stepup.core.api.plan] now works very differently:
it behaves almost like the `run()` function,
except for a few small differences:

- The first argument is now a command string, not a directory containing another `plan.py` file.
- Except for `optional` and `shell`, all `run()` arguments are supported.
  (It is hardwired to use `optional=False, shell=False`.)
- It differs from `run()` in that it assigns a higher priority to planning steps,
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

- **Increased flexibility**: You are not forced to work in a subdirectory.
  E.g., you can have `plan_a.py` and `plan_b.py` in the same directory
  and call them both from a master `plan.py`.
- **Simplicity of the API**: works like a simplified version of `run()`,
  so there are fewer concepts to learn.

## Resource Constraints (Replacement for Pools and Blocked Steps)

- The `pool()` function has been removed, and pools can no longer be defined in `plan.py`.
  Instead, you can declare the resources available on the host via an environment variable,
  e.g. `STEPUP_BUILD_RESOURCES="gpu:2,cpu:16"`
  to indicate that the host has two GPUs and 16 CPU cores.
  When defining steps, you can then specify the required resources, e.g., `resources="gpu:1,cpu:4"`,
  and StepUp will ensure that the available resources are not over-committed.
  You can override the available resources with the
  `--resources` command-line argument to `sb` if needed.

    Note that the resource names are user-specified strings and StepUp does not implement
    pre-defined resource types, such as `gpu` or `cpu`.
    These resource definitions are only used to impose constraints when deciding which steps to run.
    You could equally use `foo` and `bar` in this example and obtain exactly the same effect.

- The `block=True` argument to `step()` and
  higher-level step-generating API functions has been removed.
  Instead, use the `resources` argument with a resource that is not available on the host,
  which will have the same effect, e.g. `resources="blocked"`.

## Changed Command-Line Arguments

The `sb` command was changed to have `-j` and `--jobs` options
instead of `-n` and `--num-workers`.

The `--log-level` (`-l`) option is no longer a global option of the `stepup` command,
because only the build subcommand acts on it:
write `sb -l INFO` or `stepup build -l INFO` instead of `stepup -l INFO boot`.

The `stepup watch-update <path>` and `stepup watch-delete <path>` subcommands
were merged into `stepup wait`:
use `stepup wait -u <path>` / `--update <path>` instead of `watch-update`,
and `stepup wait -d <path>` / `--delete <path>` instead of `watch-delete`.
Bare `stepup wait`, which blocks until the builder becomes idle, is unchanged.

## Changed Environment Variable Names

The following environment variables have been renamed to have a `STEPUP_BUILD_` prefix instead of `STEPUP_`:

| Old (StepUp 3) | New (StepUp 4) |
| --- | --- |
| `STEPUP_CLEAN` | `STEPUP_BUILD_CLEAN` |
| `STEPUP_EXPLAIN_RERUN` | `STEPUP_BUILD_EXPLAIN_RERUN` |
| `STEPUP_LOG_LEVEL` | `STEPUP_BUILD_LOG_LEVEL` |
| `STEPUP_NUM_WORKERS` | `STEPUP_BUILD_JOBS` |
| `STEPUP_PERF` | `STEPUP_BUILD_PERF` |
| `STEPUP_PROGRESS` | `STEPUP_BUILD_PROGRESS` |
| `STEPUP_WATCH` | `STEPUP_BUILD_WATCH` |
| `STEPUP_WATCH_FIRST` | `STEPUP_BUILD_WATCH_FIRST` |
| `STEPUP_YAPPI` | `STEPUP_BUILD_YAPPI` |

The variable `STEPUP_SHOW_PERF` has no counterpart in StepUp 4,
because the `--show-perf` option it configured was removed.
The resource usage of each step is stored in the workflow database
and can be inspected with `stepup browse`.

## Return Codes Have Been Renumbered

The `stepup build` or `sb` command (previously `stepup boot`)
still emits a return code that is a sum of bit flags,
but the flags themselves were renumbered in StepUp 4.0,
and the "runnable" flag was dropped because nothing ever set it.

| Meaning | StepUp 3 | StepUp 4 |
| --- | --- | --- |
| Internal error (Python exception). | `1` | `1` |
| Build aborted by Ctrl-C or `SIGTERM`. | *new* | `2` |
| At least one step failed. | `2` | `4` |
| The build reported a warning (other than the ones below). | *new* | `8` |
| At least one (non-optional) step remained pending. | `4` | `16` |
| At least one step was still runnable. | `8` | *removed* |
| The scheduler was draining. | *new* | `32` |

This is a silent change for scripts:
a StepUp 3 test like `[ $(($? & 2)) -gt 0 ]` still runs under StepUp 4,
but it now tests whether the build was interrupted instead of whether a step failed.
Revisit every place where your scripts inspect `$?` after `stepup build`.
See [Return Codes](../reference/returncode.md) for the current meaning of each bit.

## Other Small Changes

- The `getinfo()` function has been renamed to `get_info()`.
- The Python interface to `render_jinja` has been split from a single `render_jinja()` function
  into two functions: `render_jinja_file()` and `render_jinja_str()`.
  See [stepup.core.render_jinja](../reference/stepup.core.render_jinja.md) for details.

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

### Single Case

In StepUp 3, a single-case script returned its planning data from `info()`:

```python
# StepUp 3: generate.py
from stepup.core.script import driver


def info():
    return {"inp": "config.json", "out": ["cos.npy", "sin.npy"]}


def run(inp, out): ...


if __name__ == "__main__":
    driver()
```

```python
# StepUp 3: plan.py
from stepup.core.api import script, static

static("generate.py", "config.json")
script("generate.py")
```

In StepUp 4, the `info()` function becomes a `plan()` function that registers the run step:

```python
# StepUp 4: generate.py
from stepup.core.api import call
from stepup.core.call import driver


def plan():
    call("./generate.py", "run", inp="config.json", out=["cos.npy", "sin.npy"])


def run(inp, out): ...


if __name__ == "__main__":
    driver()
```

```python
# StepUp 4: plan.py
from stepup.core.api import call, static

static("generate.py", "config.json")
call("./generate.py", "plan", planning=True)
```

### Multiple Cases

In StepUp 3, running the same script for several cases required the `cases()` generator,
a `CASE_FMT` template, and a `case_info()` function:

```python
# StepUp 3: plot.py
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
# StepUp 4: plot.py
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
| `filter_dependencies` | `stepup.core.utils` | `stepup.core.extapi` |
| `get_local_import_paths` | `stepup.core.utils` | `stepup.core.extapi` |

Update your imports accordingly:

```python
# StepUp 3 / early StepUp 4
from stepup.core.utils import filter_dependencies, get_local_import_paths

# StepUp 4 (current)
from stepup.core.extapi import filter_dependencies, get_local_import_paths
```

`subs_env_vars` stays in
[`stepup.core.api`](../reference/stepup.core.api.md).
It cannot move to `stepup.core.extapi`,
because that module now imports `stepup.core.api` at module level.
