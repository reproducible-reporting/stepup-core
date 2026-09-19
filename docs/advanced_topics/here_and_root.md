---
description: >-
  Use HERE and ROOT, as functions, substitution variables or environment variables,
  to set up out-of-source builds and to reference scripts in the top-level directory.
---

# `HERE` and `ROOT` Variables

<!--
SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
SPDX-License-Identifier: CC-BY-SA-4.0
-->

`HERE` and `ROOT` relate a working directory to the directory where StepUp was started,
i.e. the StepUp root:

- `HERE` is the relative path from the StepUp root to the working directory.
- `ROOT` is the relative path in the opposite direction:
  from the working directory back to the StepUp root.

The two are therefore inverse paths,
so `${HERE}/${ROOT}` and `${ROOT}/${HERE}` both normalize to the current directory `./`.

These paths can be useful in the following cases:

- For out-of-source builds, where you want to replicate the directory structure of the source material.
  (See example below.)
- To reference a local script that is stored in the top-level directory of your project:
  `${ROOT}/script.py`

## Three Ways to Use `HERE` and `ROOT`

1. **In paths passed to the StepUp API**, e.g. `copy("example.txt", "${ROOT}/public/${HERE}/")`.
   StepUp computes the values of `${HERE}` and `${ROOT}` for the directory
   to which the path is relative.
   For most functions, this is the current working directory of the calling process.
   For the `inp`, `out` and `vol` arguments of [`step()`][stepup.core.api.step]
   and the functions built on top of it, e.g. [`run()`][stepup.core.api.run],
   this is the working directory of the new step,
   so that these variables have the same meaning as in the step's command.
   They are never recorded as environment dependencies of a step.
   Variables with a path value read with [`getenv()`][stepup.core.api.getenv],
   e.g. `DST='../public/${HERE}'`, are substituted in the same way.

2. **In Python code**, with [`get_here()`][stepup.core.path.get_here]
   and [`get_root()`][stepup.core.path.get_root] from `stepup.core.path`:

    ```python
    from stepup.core.path import get_here, get_root

    path_script = get_root() / "script.py"
    ```

    This is the recommended approach in Python,
    because these functions compute the paths from the current working directory,
    which is always up to date.

3. **As environment variables**, for shell commands and programs not written in Python.
   StepUp sets `HERE` and `ROOT` for the command of each step,
   which starts in the step's working directory.
   Unlike the functions above, the environment variables are fixed values,
   which become out of date when a process changes its working directory,
   e.g. with `cd` in a shell script, `os.chdir()` or `contextlib.chdir()` in Python,
   or when it starts a child process in another directory.
   [`run_subprocess()`][stepup.core.extapi.run_subprocess] and
   [`child_env()`][stepup.core.extapi.child_env] update them for a child process
   (see [below](#tools-running-code-in-another-directory)),
   but other ways of changing the working directory leave them unchanged.

StepUp itself never reads the environment variables `HERE` and `ROOT`,
so out-of-date values cannot corrupt the workflow graph.
They only affect paths that your own code or shell commands compose with them.

## Relative Paths in the StepUp API

Relative paths passed to the StepUp API, e.g. to [`amend()`][stepup.core.api.amend],
are interpreted like relative paths passed to `open()`:
relative to the current working directory of the calling process.
As a result, relative paths are also interpreted correctly
in a child process that runs in another directory than the step itself.

StepUp works with physical paths, in which all symbolic links are resolved,
because the current working directory of a process is only known in this form.
Symbolic links to directories inside a project should therefore be avoided:
StepUp records the physical paths of files, not the paths used in your scripts.
The same applies to the values of `HERE` and `ROOT`.
Symbolic links in the path of the project directory itself are harmless.

## Example

Example source files: [`docs/advanced_topics/here_and_root/`](https://github.com/reproducible-reporting/stepup-core/tree/main/docs/advanced_topics/here_and_root)

This example represents a minimal out-of-source build,
which is nevertheless involving several files,
due to the inherent complexity of out-of-source builds.

Create a `source/` directory with the following `source/plan.py`:

```python
{% include 'advanced_topics/here_and_root/source/plan.py' %}
```

Also create a `source/sub/` directory with a file `source/sub/example.txt` (arbitrary contents)
and the following `source/sub/plan.py`:

```python
{% include 'advanced_topics/here_and_root/source/sub/plan.py' %}
```

Make the scripts executable and run everything as follows:

```bash
chmod +x plan.py sub/plan.py
sb -j 1
```

You should get the following terminal output:

```text
{% include 'advanced_topics/here_and_root/stdout.txt' %}
```

The top-level `plan.py` provides some infrastructure:
some static files and creating the public directory where the outputs will be created.

The script `sub/plan.py` uses the `${ROOT}` and `${HERE}` variables in a way
that is independent of the location of this `sub/plan.py`.
It may therefore be fixed in an environment variable, for example:

```bash
export DST='../public/${HERE}'
```

Then you can get this path in any `plan.py` as follows:

```python
from stepup.core.api import getenv

dst = getenv("DST", back=True)
```

The `back=True` option implies that the variable is a path defined globally.
If it is a relative path, it will be interpreted relative to the working directory where
StepUp was started and will be translated to the working directory of the script calling
[`getenv()`][stepup.core.api.getenv].
Any variables present in the environment variable will also be substituted once.

## Try the Following

- Modify the scripts `plan.py` and `sub/plan.py` to utilize a `DST` variable as explained above.
  To achieve this, define `DST` externally, for instance,
  by starting StepUp as `DST='../public/${HERE}' sb -j 1`.

- As a follow-up to the previous point, run StepUp with a different `DST` value.
  For example: `DST='../out/${HERE}' sb -j 1`.
  You will see that all old output files get cleaned up after the new output is created.

## Tools Running Code in Another Directory

A tool that starts a child process in another directory than its own
may update the environment variables `HERE` and `ROOT` for the child.
This is not needed to get correct dependencies in the workflow graph,
but it is useful when the child runs user-written code that is not necessarily Python,
which may combine these variables with its own relative paths.
[`child_env()`][stepup.core.extapi.child_env] returns an environment with updated variables,
and [`run_subprocess()`][stepup.core.extapi.run_subprocess] uses it automatically.
