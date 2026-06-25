# Custom API Functions

This is the most common way to extend StepUp Core.
It is essentially the same as writing helper functions
explained in the [No Rules](../getting_started/no_rules.md) tutorial.
The main difference is that the functions are bundled in a Python package,
which makes it easier to share and reuse these functions.

The following modules of StepUp Core facilitate the implementation of such new API functions:

- [`stepup.core.api`](../reference/stepup.core.api.md) provides
  Basic (and Composite) API functions.

    For example, the [`run()`][stepup.core.api.run] function,
    also used by end users to define new steps in their `plan.py` files,
    is convenient for extension developers.
    It can be used to write a specialized function to submit a step for a specific software package,
    with some extra logic to handle the software package's specific requirements.

- [`stepup.core.extapi`](../reference/stepup.core.extapi.md) contains some low-level utilities,
  which are useful for implementing new API functions.
  (These are also used internally by StepUp Core to implement the API functions in `stepup.core.api`.)

- [`stepup.core.path`](../reference/stepup.core.path.md)
  offers higher-level path manipulation utilities
  (see [Path manipulation](#path-manipulation) below).

A few things to keep in mind:

- API functions that (indirectly) call the [`step()`][stepup.core.api.step] function
  should always return the resulting [`StepInfo`][stepup.core.stepinfo.StepInfo] object.
- Keep the computational cost of the API function low.
  They should only be used to plan the execution of a step
  and not perform any of the actual work.
- If the step runs a Python script or program, make sure you keep `shell=False` in the `step()` call.
  StepUp will then run it in-process via the forkserver when available,
  without spawning a new Python interpreter.

## Command Dispatch

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

Extension developers should keep this dispatch logic in mind and
prefer `shell=False`:
either rely on the Python entry point mechanism to benefit from the forkserver,
or execute external commands directly.

## Extension utilities

The [`stepup.core.extapi`](../reference/stepup.core.extapi.md) module provides
some reusable low-level functions for extension developers.

### Environment variable substitution

Use [`subs_env_vars()`][stepup.core.extapi.subs_env_vars] in any custom API function that
accepts path arguments that may contain shell variable references (e.g. `${MYDIR}/file.txt`).
It yields a `subs` function that performs the substitution and automatically tracks which
variables were used, amending the current step's environment dependencies accordingly:

```python
from stepup.core.extapi import subs_env_vars

def my_api_function(path_inp, path_out):
    with subs_env_vars() as subs:
        path_inp = subs(path_inp)
        path_out = subs(path_out)
    # path_inp and path_out are now resolved Path objects
```

### Dependency filtering

[`filter_dependencies()`][stepup.core.extapi.filter_dependencies] filters a collection of
file paths according to the `${STEPUP_PATH_FILTER}` environment variable.
It is useful when a step dynamically discovers which local files it depends on at runtime,
and needs to report only the relevant subset back to the director.

[`get_local_import_paths()`][stepup.core.extapi.get_local_import_paths] builds on this to
return all Python modules currently loaded in `sys.modules` that pass the filter,
which is useful for scripts that need to amend their Python-import dependencies.

### Recording subprocesses

A common pattern in extension modules is to wrap an external tool in a Python script
that also handles the communication with StepUp's director process.
When the wrapper runs the external tool as a subprocess with Python's built-in `subprocess` module,
this subprocess is not visible to StepUp's director process.

To make calls to such external tools transparent,
one can execute them with [`run_subprocess()`][stepup.core.extapi.run_subprocess],
which also registers the invocation as metadata in the workflow database.

For example, the following function wraps the `typst` command-line tool:

```python
import shlex
from stepup.core.extapi import run_subprocess

def compile_typst(src, out, root):
    run_subprocess(shlex.join(["typst", "compile", "--root", root, src, out]))
```

The `cmd` argument is a single shell-quoted string.
By default, `run_subprocess()` splits the command with `shlex.split` and runs it without a shell.
With the option `shell=True`, the command is run via a shell, which allows you to use shell features
like pipes and redirections.
The string is stored and displayed verbatim and you are responsible for proper quoting.
This gives you the opportunity to prepare a convenient human-readable command string.

Wrappers that need streaming output or `Popen`-style pipe interaction
run the subprocess themselves and record it afterwards with
[`record_subprocess()`][stepup.core.extapi.record_subprocess].

## Path manipulation

StepUp uses the [`path`](https://pypi.org/project/path/) library instead of the
built-in `pathlib`, because some path affixes carry meaning that `pathlib` would discard.
The [`stepup.core.path`](../reference/stepup.core.path.md) module collects a few helpers
for working with such paths in custom API functions.
Refer to the [reference page](../reference/stepup.core.path.md) for the full signatures.
The sections below explain when they can be used.

### Affix handling

A leading `./` or a trailing `/` is significant in StepUp:

- a trailing `/` marks a directory destination,
- a local executable must contain at least one slash (e.g. `./script.sh`), and
- static-tree paths are always stored with a trailing slash.

While the [`path`](https://pypi.org/project/path/) library can handle these affixes,
it does not always preserve them when manipulating paths.
Use [`get_affixes()`][stepup.core.path.get_affixes]
to extract them before transforming a path and
[`apply_affixes()`][stepup.core.path.apply_affixes] to restore them afterwards.

### Output path construction

[`make_path_out()`][stepup.core.path.make_path_out] derives an output path from an input
path, an optional destination, and an optional new extension.
The destination may be omitted (only the extension changes), a directory (trailing `/`),
or an explicit file.
This is the reusable mechanism behind the `dst` argument of
[`copy()`][stepup.core.api.copy], and it is handy whenever an API function turns one input
file into a derived output.

### Director-relative translation

Steps run in their own working directory, while the director tracks paths relative to the
project root (`STEPUP_ROOT` / `HERE`).
[`translate()`][stepup.core.path.translate] converts a path from a step's working directory
into a director-relative path, and [`translate_back()`][stepup.core.path.translate_back]
does the reverse.
Use them when an API function exchanges paths with the director on the user's behalf.
