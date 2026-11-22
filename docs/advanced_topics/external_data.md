---
description: >-
  Make a step run at every start of StepUp with recheck=True,
  so that it can exchange data with a resource other than files on disk or in environment variables.
---

# External Data

<!--
SPDX-FileCopyrightText: 2026 Toon Verstraelen <Toon.Verstraelen@UGent.be>
SPDX-License-Identifier: CC-BY-SA-4.0
-->

StepUp is designed to work with files on disk (and environment variables) that it can see.
It cannot, by itself, check data on a remote server, or any other external resource.
However, sometimes a workflow needs to exchange data with such external resources.
StepUp's solution is to run certain steps at every start,
even if their visible dependencies did not change,
so the step itself can interact with the external resource and act when needed.
This tutorial shows how to make a step run at every start with `recheck=True`.

This feature of StepUp was primarily developed for interacting with external resources on a network,
which is illustrated in the first example below.
It is equally useful in more local contexts,
where StepUp's dependency tracking is insufficient or inconvenient,
as shown in the [Local Examples](#local-examples) section.

!!! warning "No dependencies through external resources"

    StepUp does not track dependencies through external resources.
    They are completely opaque to StepUp and the mechanism below does not even assume they exist.
    For example, if step A sends data to a remote server, and step B fetches that remote data again,
    StepUp will not know that B depends on A.
    It may thus run them in the wrong order,
    and it does not make B pending when the upload of A has changed.
    This means that interaction with external resources must sit at the edges of a workflow:
    initial data coming in or final data going out.

Other build tools have a similar feature,
for example:

- GNU Make runs the recipe of a [phony target][make-phony]
  in every invocation, regardless of the files on disk.
- doit decides whether a task is up to date with
  [`uptodate`](https://pydoit.org/dependencies.html),
  which also accepts a Python function that can look at an external resource.
- Meson considers a `custom_target()` with [`build_always_stale: true`][meson-stale]
  always out of date.
- Nextflow re-executes a process with `cache false` when a pipeline is resumed,
  as explained in [Cache and Resume](https://docs.seqera.io/nextflow/cache-and-resume).

[make-phony]: https://www.gnu.org/software/make/manual/html_node/Phony-Targets.html
[meson-stale]: https://mesonbuild.com/Reference-manual_functions.html#custom_target_build_always_stale

Unlike a Make phony target,
a step with `recheck=True` in StepUp still takes part in the dependency graph,
so the steps downstream of it are skipped when its outputs remain the same.

## Running a Step at Every Start

To make a step run at every start of StepUp, declare it with `recheck=True`:

```python
run("./download.py", out="data.txt", recheck=True)
```

A step that finds out only while it runs that it must be checked at every start,
calls [`amend(recheck=True)`][stepup.core.api.amend].
The functions [`step()`][stepup.core.api.step], [`run()`][stepup.core.api.run],
[`plan()`][stepup.core.api.plan], [`script()`][stepup.core.api.script]
and [`call()`][stepup.core.api.call] all accept `recheck`.
Like any other step, a step with `recheck=True` only runs when it is needed
and when its inputs are available.
In watch mode, it runs once per start of StepUp, not in every build phase,
unless you press `R` or run `stepup rebuild --recheck`, see [Limits](#limits).

### How It Works

The `recheck=True` feature is implemented with an environment variable.
The director sets `STEPUP_STARTUP_I`, whose value normally changes every time the director starts.
A step with `recheck=True` depends on this variable,
as explained in the tutorial on [Environment Variables](environment_variables.md).
At every start, StepUp finds a new value of the variable,
so it runs such a step again instead of skipping it.
Writing `env="STEPUP_STARTUP_I"` or calling
[`getenv("STEPUP_STARTUP_I")`][stepup.core.api.getenv] from inside the step
declares the same dependency, but `recheck=True` states the intent more clearly.

The value does not reliably identify a start,
because a start with `--offline` keeps the value of the previous one,
and a new `.stepup/graph.db` starts again at `1`.
The variable is only meant to be tracked,
so a step must not use its value for anything else, e.g. as a key for data outside `.stepup/`.
The [reference](../reference/configuration.md#environment-variables-in-step-execution)
describes the variable in full.

## Example: A Download Step

Example source files: [`docs/advanced_topics/external_data/`][example-src]

[example-src]: https://github.com/reproducible-reporting/stepup-core/tree/main/docs/advanced_topics/external_data

In this example, the directory `external/` stands in for a remote server,
so no network is needed to run it.
No step declares this directory, so StepUp does not see when it changes,
just as it cannot see changes on a real remote server.
Create it with some data:

```bash
mkdir external
printf '%s\n' 2.0 4.0 > external/data.txt
```

The following `plan.py` defines a download step that runs at every start,
and a step that summarizes the downloaded data:

```python
--8<--
advanced_topics/external_data/plan.py
--8<--
```

The download step compares the checksum of the external file with that of its output,
and copies the file only when they differ.
The part of `main()` that handles offline mode is explained in [Working Offline](#working-offline).

```python
--8<--
advanced_topics/external_data/download.py
--8<--
```

The script `report.py` writes the summary:

```python
--8<--
advanced_topics/external_data/report.py
--8<--
```

Make the scripts executable and run StepUp:

```bash
chmod +x plan.py download.py report.py
sb -j 1
```

The first build runs all steps:

```text
--8<--
advanced_topics/external_data/stdout1.txt
--8<--
```

Run `sb -j 1` again without changing anything.
The `UPDATED` line shows that `STEPUP_STARTUP_I` has a new value, so the download step runs,
but it finds that `data.txt` is up to date and leaves it untouched.
StepUp then checks `report.py`, finds that its input did not change, and skips it:

```text
--8<--
advanced_topics/external_data/stdout2.txt
--8<--
```

Now add a value to the external data and run StepUp again:

```bash
echo 9.0 >> external/data.txt
sb -j 1
```

The download step copies the new data, so `report.py` runs as well:

```text
--8<--
advanced_topics/external_data/stdout3.txt
--8<--
```

A next build without changes skips `report.py` again:

```text
--8<--
advanced_topics/external_data/stdout4.txt
--8<--
```

Only the download step runs in every build,
and StepUp skips everything downstream as long as the downloaded file remains the same.
The comparison of checksums in `download.py` avoids repeating the download itself,
which matters for large files.
If the remote server does not support checksums, the download must be repeated every time,
and StepUp will still skip `report.py` when the file is unchanged.

A download step that learns its outputs from the remote
declares them with [`amend(out=...)`][stepup.core.api.amend] in every run,
also when it skips downloading,
e.g. because `--offline` is set or the earlier downloads are still valid.

## Rules for Steps That Run at Every Start

1. **Fast and bounded when there is nothing to do.**
   The step runs in every build, so its check delays every build.
   Keep the check to a few requests, each with a timeout.
   Do not touch the network when there is nothing to check, e.g. when credentials are absent.
   Fail when credentials are present and the remote cannot be reached,
   because the user asked for something that cannot be done.
   With `STEPUP_BUILD_OFFLINE=1`, do not touch the network at all,
   as explained in [Working Offline](#working-offline).

2. **No secrets in what StepUp stores or shows.**
   Read a secret, such as an access token, with `os.environ`,
   never with StepUp's [`getenv()`][stepup.core.api.getenv], `step(env=...)` or `amend(env=...)`.
   StepUp stores the value of every variable that a step depends on in `.stepup/graph.db`,
   and shows how it changed, at startup and with `--explain-rerun`,
   e.g. as `UPDATED │ MY_TOKEN ='old' ➜ ='new'`.
   Likewise, never write a secret to an output or to the standard output or error.

3. **Undeclared state stays private.**
   A step that runs at every start may read a secret, the remote, the offline mode
   (with `is_offline()`) or files of its own.
   It does not need to declare these as dependencies, because the step runs at every start anyway.
   (With `--offline` or between rechecks, the step does not run again,
   and the workflow keeps the previous result, which is intended.)

    That said, an always-run step may declare dependencies to StepUp,
    so it runs at the right time in the workflow.
    For example, an upload script should depend on the files it uploads,
    so they are updated before being uploaded.

4. **Throttled where needed.**
   Limit the number of steps that use the same server at the same time
   with [resources](resources.md),
   because a server may refuse or slow down too many connections at once.

## Working Offline

Without network access, a step that looks at the remote at every start fails.
The [`offline`](../reference/configuration.md#build-control) setting, e.g. `sb --offline`,
lets such steps keep the results of the previous start:

- `STEPUP_STARTUP_I` keeps its value of the previous start,
  so no step runs merely because StepUp started.
  A step that runs at every start keeps its result of the previous start,
  and so does everything downstream of it.
- A step that runs anyway, because something else changed,
  finds `STEPUP_BUILD_OFFLINE=1` in its environment, and does not touch the network.
  It runs, for example, when its script changed,
  or when it failed at the previous start because the server could not be reached.
- The next start without `--offline` gives `STEPUP_STARTUP_I` a new value,
  so every step that runs at every start looks at the remote again.
- In watch mode, the `R` key and `stepup rebuild --recheck` keep the value as well,
  so it only starts a build phase, like `stepup rebuild`.

To see this in the example, change the external data and run StepUp offline:

```bash
echo 3.0 >> external/data.txt
sb -j 1 --offline
```

No step runs, so the change of the external data goes unnoticed:

```text
--8<--
advanced_topics/external_data/stdout5.txt
--8<--
```

The next start without `--offline` downloads the new data, and `report.py` runs:

```bash
sb -j 1
```

```text
--8<--
advanced_topics/external_data/stdout6.txt
--8<--
```

When the download step does run offline,
the first lines of its `main()` function keep `data.txt` and say so in the output.
When `data.txt` does not exist, the step fails with a message that says why,
instead of a network error after a timeout.

The download step checks the mode with `is_offline()`,
which reads `STEPUP_BUILD_OFFLINE` without declaring it.
An unset variable, or any value other than `1`, means online.
A step declares `STEPUP_BUILD_OFFLINE` only when its successful output depends on it,
which gives three cases:

- **A step with `recheck=True`** only calls `is_offline()`.
  The next start online runs it again anyway,
  and declaring the variable would make it run at the first start offline,
  which offline mode avoids.
- **A step that needs the network only to succeed**, e.g. for a license server,
  only calls `is_offline()`, if it checks at all.
  Its output does not depend on the mode.
  Offline it can only fail, and failed steps are retried at the next start or rebuild.
  Declaring the variable would make it run, and fail, as soon as StepUp is started offline.
- **A step that writes a different output offline**, e.g. a placeholder,
  calls `is_offline(declare=True)`, which also declares the variable.
  It must run again when StepUp is back online, to replace the placeholder.

A step that runs at every start, e.g. with `recheck=True`, cannot declare the variable.
StepUp reports this combination as an error,
which makes the step fail when it calls `is_offline(declare=True)`.

[Steps That Use the Network](../extending/api.md#steps-that-use-the-network)
asks every extension package to honor `STEPUP_BUILD_OFFLINE` in this way.

## Try the Following

- Change the docstring of `download.py` and run `sb -j 1 --offline`.
  The download step runs, because its script changed, and prints that it keeps `data.txt`.
  StepUp then skips `report.py`.

- Remove `data.txt` and run `sb -j 1 --offline`.
  The download step fails, because it cannot download the data offline.
  Run `sb -j 1` to download the data again.

## Local Examples

### Git Version

For a document developed in a Git repository,
one may want to insert the current version of the repository in the compiled PDF.
The output of `git describe` may be used for this,
but its output depends non-trivially on a number of files under `.git`.
Making those file dependencies explicit is possible, but cumbersome and error-prone.
Because `git describe` is fast, a simpler solution is to run it at every start:

```python
run(
    "git describe --tags --always --dirty > git-version.txt",
    shell=True,
    out="git-version.txt",
    recheck=True,
)
```

If the output is the same as in the previous start, dependent steps are skipped,
and the document is not recompiled.
(Drop `--dirty` if Git tracks the outputs of the build,
because the first build after a commit then modifies tracked files,
which adds the `-dirty` suffix and compiles the document a second time.)

Other Git commands fitting this pattern include
`git rev-parse HEAD`, `git log -1 --format=%cd` or `git ls-files`.

A change to the Git repository while `stepup build --watch` is running
is only picked up after you press `R` or run `stepup rebuild --recheck`,
see [Limits](#limits).

### Hardware Details

If the output of your workflow is sensitive to the hardware it runs on,
you may want to record these details.
For the CPU, this can be done as follows:

```python
run(
    "lscpu --hierarchic=always | grep -v 'CPU(s) scaling MHz' > cpu.txt",
    shell=True,
    out="cpu.txt",
    recheck=True,
)
```

The CPU frequency scaling is filtered out because it is different every time,
and not a property of the CPU itself.
(Note that this is a simplified Linux-only example.)

A more robust approach is to post-process the output of these commands,
e.g. `lscpu --json`, `lshw --json` or `nvidia-smi -L`,
with a script that extracts the relevant and stable parts,
before writing the output to a file that StepUp can track.

### Software Versions

It is generally recommended to specify the software used by a workflow through a "lock file".
Some well-known examples:

| Ecosystem | File |
| --------- | ---- |
| Python | [`pylock.toml`][pylock] |
| uv | [`uv.lock`][uv-lock] |
| Pixi | [`pixi.lock`][pixi-lock] |
| Conda | [`conda-lock.yaml`][conda-lock] |
| R | [`renv.lock`][renv-lock] |
| Node.js | [`package-lock.json`][npm-lock] |
| Julia | [`Manifest.toml`][julia-toml] |
| Rust | [`Cargo.lock`][cargo-lock] |

[pylock]: https://packaging.python.org/en/latest/specifications/pylock-toml/
[uv-lock]: https://docs.astral.sh/uv/concepts/projects/layout/#the-lockfile
[pixi-lock]: https://pixi.prefix.dev/latest/workspace/lock_file/
[conda-lock]: https://conda.github.io/conda-lock/
[renv-lock]: https://rstudio.github.io/renv/articles/renv.html
[npm-lock]: https://docs.npmjs.com/cli/v9/configuring-npm/package-lock-json
[julia-toml]: https://pkgdocs.julialang.org/v1/toml-files/
[cargo-lock]: https://doc.rust-lang.org/cargo/guide/cargo-toml-vs-cargo-lock.html

A lock file goes further than simply listing all software tools your workflow uses directly.
It also includes all dependencies, their versions,
and typically also file hashes of the source code or binaries.

If some software is installed without such a lock file,
you can still record its version at every start, e.g. with:

```python
run(
    "typst --version > typst-version.txt",
    shell=True,
    out="typst-version.txt",
    recheck=True,
)
```

## Limits

- **Once per start or recheck, not per build phase.**
  In watch mode, the value of `STEPUP_STARTUP_I` stays the same in later build phases,
  e.g. after you press `r`, so a step that runs at every start does not run again.
  To look at the external data again, press `R`,
  or run `stepup rebuild --recheck` in another terminal.
  This gives the variable a new value, as if StepUp started again,
  so the steps with `recheck=True` run in the build phase that follows.
- **Environment variables are read at startup only**, including secrets.
  The steps get the environment of the director as it was when it started,
  so a token that you set after starting StepUp is only seen after a restart.
  A recheck does not change this.
- **A step that does not handle offline mode fails offline when something else makes it run**,
  e.g. a change of its script.
  The `--offline` setting only stops steps from running merely because StepUp started.
