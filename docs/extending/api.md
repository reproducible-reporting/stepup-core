---
description: >-
  Bundle your own step-generating functions in a package,
  building on stepup.core.api, stepup.core.extapi and stepup.core.path.
---

# Custom API Functions

<!--
SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
SPDX-License-Identifier: CC-BY-SA-4.0
-->

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
  (see [Path Manipulation](#path-manipulation) below).

- [`stepup.core.exceptions`](../reference/stepup.core.exceptions.md)
  defines the exceptions raised by the functions above,
  e.g. [`PathError`][stepup.core.exceptions.PathError]
  and [`EnvVarError`][stepup.core.exceptions.EnvVarError],
  so callers can catch them selectively instead of a generic `ValueError`.

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
  The full command string is passed to a POSIX shell,
  which is `/bin/sh` on Linux and macOS
  and [one that StepUp looks for](../installation.md#posix-shell-on-windows) on Windows,
  unless the environment variable `STEPUP_SHELL` names another one.
  Shell features such as pipes and redirections are available.

- **Built-in copy** (`shell=False` and the command is exactly `cp -p SRC DST`):
  StepUp copies the file itself, without starting a process.
  This is the command of the steps that [`copy()`][stepup.core.api.copy] creates.

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

## Extension Utilities

The [`stepup.core.extapi`](../reference/stepup.core.extapi.md) module provides
some reusable low-level functions for extension developers.
One of them, `subs_env_vars()`, lives in
[`stepup.core.api`](../reference/stepup.core.api.md) instead,
because it is mutually recursive with `amend()`.

### Environment Variable Substitution

Use [`subs_env_vars()`][stepup.core.api.subs_env_vars] in any custom API function that
accepts path arguments that may contain shell variable references (e.g. `${MYDIR}/file.txt`).
It yields an [`EnvSubstitutor`][stepup.core.api.EnvSubstitutor] that performs the substitution
and automatically tracks which variables were used,
amending the current step's environment dependencies accordingly:

```python
from stepup.core.api import subs_env_vars


def my_api_function(path_inp, path_out):
    with subs_env_vars() as subs_env:
        su_path_inp = subs_env(path_inp)
        su_path_out = subs_env(path_out)
    # su_path_inp and su_path_out are now substituted and normalized Path objects
```

Calling the substitutor normalizes the result,
but a leading `./` and a trailing `/` are restored after the normalization,
because a trailing slash marks a directory in StepUp.
An affix introduced by the environment variable itself is preserved as well,
e.g. when `${MYDIR}` holds `data/`.

### Dependency Filtering

[`filter_dependencies()`][stepup.core.extapi.filter_dependencies] filters a collection of
file paths according to the `${STEPUP_PATH_FILTER}` environment variable.
It is useful when a step dynamically discovers which local files it depends on at runtime,
and needs to report only the relevant subset back to the director.

[`get_local_import_paths()`][stepup.core.extapi.get_local_import_paths] builds on this to
return all Python modules currently loaded in `sys.modules` that pass the filter,
which is useful for scripts that need to amend their Python-import dependencies.

### Recording Subprocesses

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
With the option `shell=True`, the command is run via a POSIX shell,
the same one as for a step with `shell=True`,
which allows you to use shell features like pipes and redirections.
The string is stored and displayed verbatim and you are responsible for proper quoting.
This gives you the opportunity to prepare a convenient human-readable command string.

`run_subprocess()` captures the subprocess's standard output and error into
the returned `CompletedProcess` object (`cp.stdout` and `cp.stderr`).

When `check=True`, `run_subprocess()` raises a `subprocess.CalledProcessError` if the subprocess
exits with a non-zero return code, after recording the invocation in the workflow database.
In this case, it also writes out the subprocess's standard output and error.

Wrappers that need streaming output or `Popen`-style pipe interaction
run the subprocess themselves using the built-in `subprocess` module and record it afterwards with
[`record_subprocess()`][stepup.core.extapi.record_subprocess].

When such a subprocess runs in another directory and executes user-written code,
pass it an environment from [`child_env()`][stepup.core.extapi.child_env],
as `run_subprocess()` does,
so that `HERE` and `ROOT` refer to its own working directory.
See [`HERE` and `ROOT` Variables](../advanced_topics/here_and_root.md) for more details.

## Path Manipulation

StepUp uses the [`path`](https://pypi.org/project/path/) library instead of the
built-in `pathlib`, because some path affixes carry meaning that `pathlib` would discard.
The [`stepup.core.path`](../reference/stepup.core.path.md) module collects a few helpers
for working with such paths in custom API functions.
Refer to the [reference page](../reference/stepup.core.path.md) for the full signatures.
The sections below explain when they can be used.

### The `Path` Class

Inside StepUp, the only path separator is `/`, on every platform.
[`Path`][stepup.core.path.Path] in `stepup.core.path` is a subclass of `path.Path`
that guarantees this:
on Windows, it converts backslashes to `/` and its methods return paths with `/`.
On other platforms, it behaves exactly like `path.Path`.

An extension package should import `Path` from `stepup.core.path`, not from `path`,
and should not use `os.sep` or the functions of `os.path` to manipulate paths,
because these produce backslashes on Windows.
`ConventionTests` in `stepup.core.pytest` contains a test of these rules,
which a package can skip by setting `native_path_modules = None`.

### Path Coercion

While `Path` is used internally, all end-user API functions in `stepup.core.api`
also accept `os.PathLike` objects (i.e. `pathlib.Path`) as path arguments.
To facilitate the conversion, the module `stepup.core.path` provides the following:

- `StrPath`: a type alias for `Union[str, os.PathLike]` that can be used in type hints.
- `coerce_path()`:
    converts a single path argument to `Path`.
- `coerce_paths()`:
    converts a single path argument or a collection of path arguments
    to a list of `Path` objects.
- `coerce_paths2()`:
    converts a list of path arguments or lists thereof to a list of `Path` objects.
- `coerce_str()`:
    converts a single path argument to `str`, preserving any trailing slash.

### Affix Handling

A leading `./` or a trailing `/` is significant in StepUp:

- a trailing `/` marks a directory destination,
- a local executable must contain at least one slash (e.g. `./script.sh`), and
- static-tree paths are always stored with a trailing slash.

While the [`path`](https://pypi.org/project/path/) library can handle these affixes,
it does not always preserve them when manipulating paths.
Use [`get_affixes()`][stepup.core.path.get_affixes]
to extract them before transforming a path and
[`apply_affixes()`][stepup.core.path.apply_affixes] to restore them afterwards.

### Output Path Construction

[`make_path_out()`][stepup.core.path.make_path_out] derives an output path from an input
path, an optional destination, and an optional new extension.
The destination may be omitted (only the extension changes), a directory (trailing `/`),
or an explicit file.
This is the reusable mechanism behind the `dst` argument of
[`copy()`][stepup.core.api.copy], and it is handy whenever an API function turns one input
file into a derived output.

### Director-Relative Translation

Steps run in their own working directory, while the director tracks paths relative to the
project root (`STEPUP_ROOT`).
[`translate()`][stepup.core.path.translate] converts a path from a step's working directory
into a director-relative path, and [`translate_back()`][stepup.core.path.translate_back]
does the reverse.
Use them when an API function exchanges paths with the director on the user's behalf.

## Steps That Use the Network

A step that downloads or uploads data depends on a remote server, which StepUp does not watch.
An extension package that defines such steps follows these conventions,
so that the steps look at the remote when needed and a build without network access still works:

1. **Track `STEPUP_DIRECTOR_I`** in every step that looks at a remote,
   by adding `env="STEPUP_DIRECTOR_I"` in the API function that defines the step.
   The step then runs at every start of StepUp,
   see [`STEPUP_DIRECTOR_I`](../reference/configuration.md#environment-variables-in-step-execution).
   When there is nothing to do, it leaves its outputs untouched,
   so that StepUp skips the steps that use them.
2. **Honor `STEPUP_BUILD_OFFLINE`.**
   When it is `1`, set by the [`offline`](../reference/configuration.md#build-control) setting,
   the step does not touch the network at all, and says so in its output.
   An upload sends nothing and succeeds.
   A download keeps the outputs that it can vouch for,
   e.g. because a record of its last download shows that they are complete,
   and declares again the outputs that it declared with [`amend()`][stepup.core.api.amend],
   because StepUp drops them when a step runs again.
   When it cannot vouch for an output,
   it fails with a message that says why, instead of a network error after a timeout.
3. **Read `STEPUP_BUILD_OFFLINE` with `os.environ`** in a step that tracks `STEPUP_DIRECTOR_I`,
   without declaring it as a dependency.
   The next start without `offline` runs the step again,
   so a result that it produced offline is never kept online.
   Declaring the variable would make the step run at the first start with `offline`,
   which is exactly what this setting avoids.
   Any other step that behaves differently offline declares the variable,
   like any variable that it reads.
4. **Treat an unset `STEPUP_BUILD_OFFLINE` as online**,
   so that a console script also works when it is run by hand,
   or by a version of StepUp Core that does not set the variable.
5. **Keep secrets out of what StepUp stores or shows.**
   Read a secret, such as an access token, with `os.environ`,
   never with [`getenv()`][stepup.core.api.getenv] or `env=`,
   because StepUp stores the values of the variables that a step depends on
   and shows how they changed.
   For the same reason, never write a secret to an output or to the standard output or error.

For example, an API function defines a download step that runs at every start:

```python
from stepup.core.api import run, shq


def download(url: str, dst: str):
    return run(f"my-download {shq(url)} {shq(dst)}", out=dst, env="STEPUP_DIRECTOR_I")
```

The console script `my-download` (see [Console Scripts](console_scripts.md)) starts as follows:

```python
import os
import sys


def main():
    url, dst = sys.argv[1:]
    if os.environ.get("STEPUP_BUILD_OFFLINE") == "1":
        if not os.path.isfile(dst):
            sys.exit(f"Working offline, so {dst} cannot be downloaded.")
        print(f"Working offline, keeping {dst}.")
        return
    # Download the data, and rewrite dst only when its content differs.
    ...
```

## Supporting Windows

StepUp Core runs on Windows, see [Supported Platforms](../platforms.md).
An extension package that should also run on Windows needs more than the
path test of `ConventionTests` (see [The `Path` Class](#the-path-class)),
which only finds the most common mistakes.
Check the following points as well:

- **Native paths.**
  `os.getcwd()`, `tempfile`, `shutil.which()` and `pathlib` give paths with backslashes.
  Wrap them in `Path` before they are stored, compared or put in a command.
  Pass a native path to a program only when the program needs one.
- **Relative paths across drives.**
  `Path.relpath()` raises a `ValueError` for two paths on different drives.
  Keep such a path absolute.
- **Paths in commands.**
  Embed them with [`shq()`][stepup.core.api.shq], never with an f-string around a native path,
  because a backslash in a command is an escape character.
- **Text files.**
  Open them with `encoding="utf-8"` and write them with `newline="\n"`.
  Otherwise Python 3.14 and older use the ANSI code page and `\r\n` on Windows,
  which changes what is read and the digests of what is written.
  Ruff reports a missing encoding with the preview rule `PLW1514`.
- **POSIX-only modules and features.**
  `fcntl`, `termios` and `resource` cannot be imported on Windows,
  and some libraries do not offer every transport there.
  For example, the Windows wheels of pyzmq have the `ipc` transport disabled
  (as of pyzmq 26.2).
  Test for the feature, not for the platform.
- **Shell commands.**
  A step with `shell=True` needs a POSIX shell, which a Windows user must install.
  A script with a `python3` shebang line that is started by a shell command,
  e.g. `./work.py > out.txt`, needs the `python3` wrapper described in
  [Shell Commands](../platforms.md#shell-commands).
- **Tests.**
  Load `stepup.core.pytest` as a pytest plugin in `conftest.py`
  (`pytest_plugins = ("stepup.core.pytest",)`),
  so that pytest reports the output of failed examples legibly on Windows.
  Add a `.gitattributes` with `* text=auto eol=lf`,
  so that a Windows checkout does not change the line endings of files and shell scripts,
  which would also change their digests.
  `run_example()` already provides a `python3` wrapper for the `main.sh` of an example.
