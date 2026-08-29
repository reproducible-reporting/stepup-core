---
description: >-
  Release notes for every version of StepUp Core,
  following Keep a Changelog and effort-based versioning.
---

# Changelog

<!--
SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
SPDX-License-Identifier: CC-BY-SA-4.0
-->

All notable changes to StepUp Core will be documented on this page.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Effort-based Versioning](https://jacobtomlinson.dev/effver/).
(Changes to features documented as "experimental" will not increment macro and meso version numbers.)

## [Unreleased][]

## [4.0.0rc14][] - 2026-08-25 {: #v4.0.0rc14 }

StepUp 4 is a major redesign to make workflows more expressive to write,
cheaper to run and more transparent to debug and analyze:

- **Writing the Workflow:**
    - A single `run()` replaces `runsh()` and `runpy()`.
    - Only `static()` declares *static* files.
    - `glob()` merely queries static files, which can be repeated safely.
    - `call()` has been simplified and made more powerful than the old `call()` and `script()` functions.
    - Directories are no longer tracked explicitly.
    - Static trees cover large data directories efficiently.
- **Running it.**
    - A critical path scheduler optimizes workflow execution
      by prioritizing steps with the longest tail time.
    - `resources` and `hold()` control which steps run simultaneously.
    - Hashes of static files are computed in parallel by the same scheduler.
    - `SOURCE_DATE_EPOCH` is fixed by default for reproducible outputs.
    - A build can be restricted to targets, and steps can be optional.
    - Layered config files control StepUp's runtime behavior,
      and `stepup config` shows the merged configuration.
- **Dealing with errors.**
    - A mistake the user can fix is a short `ERROR:` message instead of a traceback.
    - Pending steps are summarized by root cause.
    - The output and subprocess invocations of every step are stored for `stepup browse`.
    - `Ctrl-C`, `Ctrl-Z` and `SIGTERM` just work, without leaving orphaned processes behind.

Two architectural shifts underpin these improvements.

1. The director no longer starts worker processes:
   each step is a highly optimized asyncio task
   that cannot be outlived by the child process it executes,
   so the startup cost no longer grows with the degree of parallelism
   and no step can affect a later one through leftover process state.
2. Much of the bookkeeping moved from Python into SQLite:
   triggers maintain the derived columns that the scheduler reads,
   cascades and constraints enforce the graph invariants,
   and selecting the next step to dispatch is a single query.
   Database transaction locking rules out entire classes of race conditions.
   The whole redesign is backed by a test suite with more than four times as many unit tests
   and over 1.5 times as many integration examples.

A [migration guide](migration/from_3x_to_40.md) shows the way up from StepUp 3,

(This is release candidate 14 of the upcoming StepUp Core 4.0 release.
Note that all changes of the release candidates are combined below.
This section is treated as a draft of the changelog for the final 4.0.0 release,
and will be updated with any further changes before the final release.)

### Added

#### Command Line and Configuration

- StepUp can now also be configured through configuration files,
  in addition to environment variables and command-line arguments.
  See [Configuration files](reference/configuration.md) for details.

- The `stepup config` command shows the current configuration,
  as the result of merging all config files and environment variables.
  It also lists the `STEPUP_*` environment variables in three groups,
  separating the ones it recognizes as settings from the ones used internally
  and from the ones without any effect,
  because the name of an environment variable cannot be checked the way a config key is.

- Mistakes in a config file or a `STEPUP_*` environment variable are reported as a list of
  short messages, so all problems are shown at once, each naming the file or variable to fix.
  Unknown sections and keys are also detected, with a suggestion where a key belongs
  or how it is spelled correctly.
  The `stepup config` command is the exception that still runs,
  so the configuration can be inspected precisely when it is broken.
  It shows each problem on the line of the setting, section or config file it concerns.
  Problems are shown in red when the terminal supports color.

- `stepup build [targets...]` restricts the build to the steps needed to produce
  the given output files (and their dependencies), instead of the full default workflow.
  A target cannot name a volatile output or a static file, and a target that is never
  produced by any step is reported as a warning at the end of the build.
  A target may also name a directory (a path ending in `/`),
  which elevates every step whose declared need is `Need.DEFAULT`
  and whose output falls under that directory, best-effort (never raises).
  Automatic cleaning is disabled when targets are specified.
  See [Build Targets](advanced_topics/build_targets.md) for details.

#### Build Execution and Process Control

- StepUp can use a forkserver for Python step execution,
  which reduces startup overhead.
  This can be controlled with the `--forkserver` flag,
  which is enabled by default on Linux.

- Added `--preload-modules` option to `sb` to specify a comma-separated list of Python
  modules to be pre-loaded into the forkserver.
  This only has an effect when `--forkserver` is active and can speed up workflows
  that repeatedly import large modules.

- When the first word of a `run()` command is a bare command name matching a `console_scripts`
  entry point from the current Python environment, StepUp now runs it as a Python entry point:
  when the forkserver is enabled (`--forkserver`), the entry point function is called
  in-process rather than spawning a new subprocess, reducing overhead.
  If the entry point belongs to a different Python environment,
  a warning is written to the step's standard error and
  the command falls back to direct subprocess execution.

- A `run()` or `step()` command may start with `VAR=value` assignments
  (when `shell=False`), e.g. `run("OMP_NUM_THREADS=4 ./work.py")`.
  These are applied as step-specific environment variable overrides when the step runs,
  which is otherwise impossible without a shell.
  The overrides are part of the step hash, so changing a value reruns the step.
  A variable cannot be both an override and an `env` dependency.
  The `step()` function also accepts the overrides directly,
  as a dictionary passed to its new `env_overrides` argument.

- Added a `--fix-epoch` option to `sb` (on by default)
  to set the `SOURCE_DATE_EPOCH` environment variable to a fixed value for all step executions.
  This is useful for ensuring reproducible builds.
  See [Configuration files](reference/configuration.md) for details.

- Added a `--cgroup` option to `sb` (off by default) that launches the director
  in a `systemd-run --scope` cgroup of its own,
  so the peak memory of the director and all its step processes together is measured
  and included in the resource usage report at the end of `.stepup/director.log`.
  This requires Linux with cgroup v2 and `systemd-run`,
  and fails when they are not available.

#### Scheduling

- `step()` accepts a new `duration` argument:
  an initial estimate (in seconds) of the step's wall time,
  used by the scheduler (when `--duration` is enabled) to prioritize execution order.
  All step-generating API functions (`run()`, `script()`, `call()`, `render_jinja()`, etc.)
  also accept a `duration` argument.
  See [Duration and Hold](advanced_topics/duration_and_hold.md) for details.

- New `hold()` context manager in `stepup.core.api`, for a step (typically a `plan.py`) to
  wrap a batch of declarations so the steps declared inside are held back from dispatch
  until the block closes, instead of each being dispatched as soon as it is declared.
  This lets the whole batch become simultaneously eligible and get sorted by `_tail_time`
  once released, so slow steps declared late no longer lose the race for job slots to
  fast steps declared early. `hold()` is re-entrant: nested `with hold():` blocks for the
  same step (e.g. through a shared helper function) compose correctly, with steps staying
  held back until the outermost block exits.
  See [Duration and Hold](advanced_topics/duration_and_hold.md) for details.

- New `resources` argument of `step()` and all step-generating API functions,
  which limits how many steps run concurrently
  when full parallelization would be counterproductive,
  e.g. because a program misbehaves when several instances run at once,
  because steps compete for memory or GPUs,
  or because a license caps the number of instances.
  The available quantities are declared with the new `--resources` option of `sb`
  or the `STEPUP_BUILD_RESOURCES` environment variable.
  See [Resources](advanced_topics/resources.md) for details.

- The "rescheduling" mechanism of StepUp 3 has been replaced by a simpler "defer" mechanism,
  with a new `--defer-cap` option (default 100) that fails a step
  once it has been deferred that many times in a row without succeeding.
  This acts as a livelock guard for `amend()`-driven defers.

#### Workflow API

- All functions in `stepup.core.api` now accept `os.PathLike` objects (i.e. `pathlib.Path`)
  as path arguments, in addition to `str` and `path.Path`.

- The `command` argument of `step()`, `run()` and `plan()` may now be a callable
  that builds the command from the step's own paths,
  so a path list no longer has to be named twice:

    ```python
    run(lambda out: f"./gen.py {shq(out)}", out=["out1.txt", "out2.txt"])
    ```

    The callable may declare any subset of the parameters `inp`, `out` and `vol`,
    matched by name, and receives the paths after environment variable substitution
    and normalization.
    The new `shq()` function in `stepup.core.api` quotes one or more paths for safe shell usage.
    The type of the argument, a path or such a callable,
    is the new public alias `CommandArg` in `stepup.core.api`.

- New `dumpns()` function in `stepup.core.api`, the counterpart of `loadns()`.
  It writes a `dict` or `SimpleNamespace` to a JSON or YAML file
  and amends the file as an output of the calling step by default.
  Values of types that `cattrs` understands (`attrs` classes and dataclasses)
  are unstructured automatically.

#### Terminal Output and Inspection

- StepUp now stores the captured stdout and stderr of each step in the workflow database,
  so they can be inspected after the build.
  Output from subprocesses launched by a forked Python step is captured properly.
  The amount stored per stream can be capped with the new `STEPUP_MAX_OUTPUT_SIZE`
  environment variable (`0` = unlimited, the default).
  These outputs can later be viewed with `stepup browse`.

- The `stepup browse` command takes two new options:
  `--browser` to pick a browser and `--no-open-browser` to only print the URL.
  Its existing `--port` option now defaults to `7837` instead of `8000`
  and can also be set with `STEPUP_BROWSE_PORT` or the `[browse]` section of a config file.
  It now also opens the browser for you, and works with text-mode browsers:
  a graphical browser gets a new tab while the server keeps serving until `Ctrl-C`,
  and a text-mode browser (such as `lynx` or `w3m`) runs cleanly in the terminal,
  after which the server stops as soon as the user closes it.
  Its pages also show more information about a step:
  the step digests, the tail time used by the scheduler,
  the named glob patterns of a step and the static trees in the workflow.

- A resource usage report is shown at the end of the file `.stepup/director.log`.
  Its peak memory line for the director and its children relies on Linux control groups,
  so it is only filled in when `sb --cgroup` is used on a supported system.

- Each build phase ends with a `Ran N job(s).` message,
  counting only the jobs that executed a step's command.
  Skipped steps and internal validation jobs are not included.

- Added a `--sqllog` option to `sb` that appends per-query timings to a file
  and writes a query, call site and query plan index when the director exits,
  to check query plans and execution times.

- Added a `--joblog` option to `sb` to log the start and end of each job to a file.

#### Extensions and Internals

- New `stepup.core.exceptions` module collecting the exceptions raised by StepUp,
  organized in a hierarchy that separates a mistake the user can fix
  (`UsageError`, with `ConfigError`, `ToolError`, `GraphError` and `StepUpError` below it)
  from a bug in StepUp (`ConsistencyError` and the other internal errors).
  See [stepup.core.exceptions](reference/stepup.core.exceptions.md) for the full reference.

- New `stepup.core.extapi` module for StepUp extension developers,
  collecting utilities previously buried in `stepup.core.utils`.
  See [stepup.core.extapi](reference/stepup.core.extapi.md) for the full reference
  and [Custom API Functions](extending/api.md) for usage guidance.
  One utility aimed at extension developers stays in `stepup.core.api`,
  because `stepup.core.extapi` is built on top of it: `subs_env_vars`.

- Extension wrapper steps can now record the exact subprocess invocations they make,
  using `run_subprocess` in `stepup.core.extapi`,
  which executes the subprocess and records its invocation.
  Alternatively, `record_subprocess()` can be used to record a subprocess that was already executed,
  e.g. using the built-in `subprocess` module.
  The command line, working directory, environment overlay, shell flag, return code
  and captured standard input, output and error are stored in a
  new `step_subprocess` table for debugging and archival.
  Recorded invocations are shown in `stepup browse`, formatted as shell-pasteable command lines.
  See [Custom API Functions](extending/api.md) for implementation guidance.

### Changed

#### Project and Documentation

- Relicense the StepUp Core source code under `LGPL-3.0-or-later`.
  This clarifies that users of StepUp can assign any license of their choice
  to the workflows they create with StepUp (e.g., `plan.py` and related files).
  This has always been the intention, but with this change, it becomes legally explicit.
  The repository is now also [REUSE](https://reuse.software/) compliant:
  every file carries an SPDX copyright and license header,
  with the documentation under `CC-BY-SA-4.0` and the logo under a license of its own.

- A `CITATION.cff` file was added,
  so StepUp Core can be cited with the metadata that GitHub and reference managers read from it.

- Documentation has been updated to reflect the API changes and to clarify some other points:
    - All tutorials have been updated to reflect the new API and workflow.
    - A [migration guide](migration/from_3x_to_40.md) has been added
      to help users migrate from StepUp 3 to StepUp 4.

- `cattrs` was added as a runtime dependency.
  It is used to convert hashes, named globs, configuration values and the arguments
  of `call()` to and from JSON or YAML.
  The minimal version of `attrs` was raised to 23.1.0 for the same reason.

#### Command Line and Configuration

- `stepup boot` has been renamed to `stepup build`
  and can be called conveniently with the `sb` shortcut.
  The `boot` command will be removed in a future release.

- The `--num-workers` / `-n` option of `sb` has been renamed to `--jobs` / `-j`,
  in line with the convention used by `make` and similar tools.
  The environment variable changes from `STEPUP_NUM_WORKERS` to `STEPUP_BUILD_JOBS`,
  and the config-file key is `jobs` in the `[build]` section.
  Because StepUp 4 no longer launches worker processes,
  the option caps how many steps run concurrently.
  The default value is now `1.0` (one job per CPU core) instead of `1.2`.
  (The old value is common for I/O-bound build workflows,
  but StepUp is more commonly applied to CPU-bound workflows,
  for which the new default is more suitable.)

- The `stepup run` subcommand is renamed to `stepup rebuild`,
  because the *run phase* it referred to is now called the *build phase*.
  The keyboard shortcut in the terminal user interface is still `r`.

- The `stepup watch-update <path>` and `stepup watch-delete <path>` subcommands
  have been merged into `stepup wait`, as `stepup wait -u <path>` / `--update <path>`
  and `stepup wait -d <path>` / `--delete <path>` respectively.
  Bare `stepup wait` still waits for the builder to become idle.

- The `stepup status` subcommand reads the workflow database directly
  instead of asking the director over remote procedure calls,
  so it also works when no build is running.
  Besides the step and file counts, it now also lists the resources
  held by the running steps.

- Return codes have changed.
  The new return code bits are documented in [StepUp Return Codes](reference/returncode.md).
  The changes compared to StepUp 3 are summarized in the [migration guide](migration/from_3x_to_40.md#return-codes-have-been-renumbered).

- The `--log-level` / `-l` option has moved from the `stepup` command to its `build`
  subcommand, which is the only one acting on it:
  write `sb -l INFO` or `stepup build -l INFO` instead of `stepup -l INFO build`.
  The environment variable changes from `STEPUP_LOG_LEVEL` to `STEPUP_BUILD_LOG_LEVEL`,
  and the config-file key is `log_level` in the `[build]` section.
  The director exports the level to its steps under the new name as well.

- The default of `${STEPUP_PATH_FILTER}` is broadened from `-venv` to
  `-.venv:-venv:-.tox:-.nox:-.direnv:-.pixi:-node_modules`,
  so the directories in which common tools install dependencies
  are ignored without having to configure the filter.

- Several environment variables have been renamed for consistency.
  See [Configuration files](reference/configuration.md) for the current names
  and [Changed Environment Variable Names](migration/from_3x_to_40.md#changed-environment-variable-names)
  in the migration guide for the full mapping.

#### Build Execution and Process Control

- Steps no longer run in worker processes that are launched up front.
  In StepUp 3, `--num-workers` decided how many workers were started at the beginning of a build,
  each of which stayed alive to execute one step after another.
  In StepUp 4, the director runs every step as an asyncio task of its own,
  and a child process is created only for the duration of the step's command.
  The `--jobs` option is therefore a limit on the number of concurrent steps,
  no longer a number of processes to start.
  This has three practical consequences:

    - The startup cost of a build no longer grows with the degree of parallelism.
      A high setting, such as `-j 128` on a large HPC node,
      no longer pays the launch time and the memory of that many long-lived processes.
    - A step can no longer affect a later step through state left behind in a process.
      An action in StepUp 3 was executed inside the worker,
      so one that imported modules, changed globals or installed signal handlers
      could corrupt the worker and interfere with every step that worker ran afterwards.
      Each step now starts from a clean process,
      which is what made it possible to remove the action abstraction layer.
    - The efficiency that the in-process actions of StepUp 3 offered is retained by
      the forkserver, from which a Python step or a console script entry point is forked,
      instead of paying for a full interpreter startup.

- The CPU detection (when `-j` is given as a float) has been extended.
  It now tries, in order:

    1. The number of cores available within the current cgroup (cgroup v2 only).
    2. Job-scheduler CPU-related environment variables (SLURM, PBS).
    3. The CPU affinity mask reported by the operating system.
    4. The total number of CPUs reported by the operating system.

    The first source that yields a usable value is used.

- Every step now runs in a session of its own,
  so a `Ctrl-C` in the terminal no longer reaches step processes directly.
  The director is the only thing that stops them, on every route.
  As a result, aborting a build also stops the actual work of a shell step
  that is a pipeline or an `&&`-chain,
  which previously kept running because only its surrounding `sh` was signalled.

#### Scheduling

- After a step fails, the scheduler now drains by default, like `make` without
  `-k` (steps already running still finish; no new steps are started).
  Use the new `--keep-going` / `-k` flag (or `STEPUP_BUILD_KEEP_GOING`) to restore the
  previous behavior of continuing to build every step whose inputs remain available.
  A drained build sets a [return code](reference/returncode.md) bit of its own,
  because it does not report the steps left pending.

- The scheduler has been replaced by a new and more efficient implementation,
  which also improves how steps are prioritized:

    - Steps are prioritized using the *tail time*, which results in the shortest overall
      execution time of the workflow.
      This is also known as critical path scheduling.
      Since StepUp assumes no full knowledge of the workflow,
      the tail time estimates are updated dynamically as new edges are discovered.
    - A new step that has not been executed before is assigned a duration of 1 second.
      When restarting StepUp, the duration of steps from previous runs is used,
      even if inputs changed, so that the scheduler can make better tail time estimates.

#### Workflow API

- The `static()` and `glob()` functions have been redesigned from scratch to permit more use cases
  while still imposing the same safety and correctness guarantees as in StepUp 3.
  The two roles are now cleanly separated:
  `static()` declares and owns, while `glob()` only queries.
  Two consequences of the redesign are worth stating here:

    - `static()` also accepts glob patterns (e.g. `static("data/*")`)
      and `NamedGlob` objects returned by `glob()`,
      next to the literal paths it took in StepUp 3.
    - `static()` returns a sorted list of the files it declared
      and the static tree roots it registered, where it used to return nothing.

    See [`static()` and `glob()` Have New Roles](migration/from_3x_to_40.md#static-and-glob-have-new-roles)
    and [Directory Handling](migration/from_3x_to_40.md#directory-handling)
    in the migration guide for details.

- The `runsh()` and `runpy()` functions have been replaced by the more flexible `run()` function.
  The new implementation is more efficient and automatically tracks local scripts as dependencies.

- The `plan()` function has been made maximally similar to `run()`,
  and now accepts arbitrary local Python scripts,
  not just a directory that must contain a `plan.py` script.

- Redesigned `call()` interface:
  the old inp/out/pickle argument modes are replaced
  by explicit function dispatch and optional `args_file` support for file-based argument passing.
  The executable and function name are positional-only parameters, `executable` and `function`,
  so that keyword arguments with those names can be forwarded to the called function.

    A function called through `call()` receives its keyword arguments
    converted to the types in its signature, using `cattrs`.
    An argument that cannot be converted is reported as a `TypeError`
    that names the argument and the expected type.
    A script that uses `stepup.core.call.driver()` as main function is self-documenting:
    running it without a function name prints one suggested command line
    for every function it exposes.

    See [Function Calls](getting_started/call.md) for details.

- The `step()` function takes a `need` argument instead of the boolean `optional` argument,
  with the levels `Need.OPTIONAL`, `Need.DEFAULT`, `Need.TARGET` and `Need.PLAN`.
  The level `Need.TARGET` cannot be declared:
  StepUp derives it for the steps needed to produce the given
  [build targets](advanced_topics/build_targets.md).
  The higher-level API functions still take `optional=True` and translate it.
  The need level of the running step is exported as `STEPUP_STEP_NEED`,
  which lets StepUp warn on standard error when a planning step is registered
  by a step that is not a planning step itself, which is usually an authoring mistake.

- The `getinfo()` function has been renamed to `get_info()`.

- `loadns()` returns a `SimpleNamespace` instead of an `argparse.Namespace`.

- `amend()` now silently ignores information that the step's plan already declared for it,
  just like it ignores information from an earlier `amend()` call of the same step.
  This lets a plan declare up front what a step also discovers while it runs,
  which improves scheduling (the step is not dispatched before its inputs are available)
  without the step having to know what was declared for it.
  Each argument is matched against its own kind only:
  amending an `out` path that was declared as `vol`, or vice versa, is still an error.

#### File Tracking and the Workflow Graph

- File hashes are computed in concurrent hash threads instead of the old serial client-side delegation.
  Similarly, the director uses the same mechanism to compute file hashes in parallel on startup.

- The "deferred glob" has been replaced by a simpler "static tree" concept.
  Files in a static tree become static only when they are used as inputs.
  This allows for huge static data directories, of which only some are used,
  without having to glob the entire directory recursively.
  To declare a static tree directory, just pass it as an argument to the `static()` function.
  Static trees interact with `static()` and `glob()` as follows:

    - A tree is the sole owner of the files under it,
      so declaring both a tree and a file it contains is decided by who declares them,
      not by the order in which they are declared.
      One step declaring both is a no-op in either order:
      the file is handed over to the tree, which becomes its creator.
      Doing so from two different steps raises in either order.
    - `glob()` declares nothing at all,
      so it never competes with a static tree for ownership of a match.
      This makes overlapping `glob()` calls over the same static tree work:
      declare the tree once with `static()`, then `glob()` it as often as needed.
    - A `glob()` match that no `static()` declaration justifies, directories included,
      is reported as a warning at the end of the build phase, not as an error,
      because the plan that would declare it may not have run yet.

- The database schema version has been incremented to 5 because:

    - Directories are no longer stored in the database
      (except for static trees, which are stored as special nodes in the graph.)
    - The BLAKE2b hash has been replaced by the more common SHA-256.
    - The `step` table and all its satellite tables have been redesigned
      to support and optimize the new scheduling algorithm.
    - Step labels no longer carry an action-name prefix.
      They store the raw command line.
    - The step state `QUEUED` has been removed, as it is no longer needed.
    - A new step state `CHECKING` has been added
      for steps that are being hash-checked for possible skipping.
    - File states are now classified into three roles:
      `STATIC`, `OUTPUT` and `VOLATILE`.
      A role does not change during a build, while a state may.
      Related file state changes:
        - `UNCONFIRMED` has been added to distinguish truly missing files
          from those who still need to be hash-checked.
        - `AWAITED` has been split into `UNDECLARED` (no role yet) and `PLANNED` (to be built).
        - `STATIC` has been renamed to `CONFIRMED`,
          so that state names no longer overlap with role names.
    - `step_outcome` and `step_subprocess` tables were added.
    - The `step` table now tracks re-entrant `hold()`/`release()` calls,
      needed for the new `hold()` context manager.
    - All hashes are stored as human-readable JSON blobs.
    - Named-glob data is stored as JSON in the new `nglob` table,
      instead of as a pickle blob in `nglob_multi`,
      for consistency and readability.

- Other changes to the workflow database, which do not alter what it stores:

    - A substantial part of the bookkeeping moved from Python into the database itself.
      The schema defines twenty triggers, where StepUp 3 had none.
      Some maintain the derived columns that the scheduler queries,
      such as the flags marking which steps must have their readiness recomputed
      after a file state, dependency or hash changed.
      Others abort the transaction when a write would violate a graph invariant,
      where Python used to check the same condition after the fact.
    - SQLite's `ON DELETE CASCADE` feature is now used for all satellite tables of the `step` table,
      so removing a step cannot leave rows of its own behind.
    - `CHECK` constraints reject an inconsistent row at the point where it is written.
    - Selecting the next step to dispatch is a single query over these columns,
      instead of Python-side bookkeeping of ready steps.
    - The UInt64 adapter and converter were removed,
      since no value is stored as a raw integer blob any more.
    - Indexes were tuned.
    - The auto_vacuum mode was set to INCREMENTAL,
      which is paired with a database vacuum worker to reclaim space from deleted nodes.

- The text output of the workflow graph, written by `stepup graph` or the `g` key,
  has changed in several ways:
  the relations are labeled `creator`, `product`, `source` and `sink`
  instead of `created by`, `creates`, `consumes` and `supplies`,
  there are no more directory nodes,
  static trees appear as nodes of their own,
  the glob patterns of a step are labeled `nglob`,
  the environment variables of a step are labeled `using_env` instead of `env_var`,
  a dynamic dependency or environment variable is marked `[dynamic]` instead of `[amended]`,
  a step also shows its `need` level,
  and the relations of a node are always written in the same order.
  Test suites of extension packages that compare this output must regenerate their
  expected files.

#### Error Reporting

- A mistake that the user can fix is reported as a short `ERROR:` message
  with return code `1`, instead of a Python traceback.
  This covers every `stepup` subcommand, a step that uses `stepup.core.api` incorrectly,
  and a tool that raises `ToolError`,
  including before the director has started, e.g. for an invalid `stepup build` target.
  It used to be implemented separately by a few subcommands,
  so `stepup status`, `stepup browse` and `stepup clean` still ended with a traceback
  in a directory where StepUp had never run.
  Errors that indicate a bug in StepUp keep their full traceback,
  and `STEPUP_DEBUG=1` shows the complete traceback of any error.
  Stopping a subcommand with `Ctrl-C` is also reported as a message now,
  and sets the `2` bit of the [return code](reference/returncode.md).

- Two declarations claiming the same file are now reported in terms of the plan
  instead of the internal graph representation.
  The message names both declarations and how to resolve the conflict, e.g.
  `File (b.txt) cannot be both declared static by step (./plan.py) and built by
  step (cp -p a.txt b.txt).`
  This covers every combination of a `static()` declaration, a step output and a
  volatile output, and the message does not depend on which declaration came first.
  Defining the same command twice in the same working directory is reported likewise,
  as is registering the same static tree from two different steps.

- At the end of every build, StepUp scans `.stepup/director.log` for symptoms of internal
  problems: logged errors, unawaited coroutines, tasks destroyed while still pending,
  and exceptions that escaped a callback, a thread or a destructor.
  None of these make the director exit with a non-zero return code by themselves,
  so the log is the only place where they can be picked up.
  The offending lines are now shown with the warning,
  which previously only mentioned that errors had been logged.
  With `STEPUP_DEBUG`, such findings are reported as an error instead
  and set the internal error bit of the return code.

#### Terminal Output and Inspection

- The end-of-build pending report no longer prints one `PENDING Step` page per pending step.
  Instead, it summarizes the **root causes** as a fixed-size ranked report: the unavailable
  input files and blocked resources that account for the most pending steps, plus a
  count of steps blocked by failed steps, waiting on each other, deferred, or otherwise
  unexplained. Use `stepup browse` to inspect the individual steps behind any entry.
  See [Blocked Steps](advanced_topics/blocked_steps.md) for details on the new format.

- A step command is escaped when it is printed to the terminal:
  a control character, such as a newline in a shell command, is written as a
  `$'\n'`-style escape.
  The reporter therefore uses one line per step,
  and the command can be copied from the terminal and pasted into a shell as is.

- The keys of the terminal user interface are listed one per line with a short description
  of what they do, instead of on a single line with only the key names.

#### Extensions and Internals

- `subs_env_vars()` yields an `EnvSubstitutor` instead of a plain function.
  It is still called the same way, but it now also normalizes the substituted path.
  A leading `./` and a trailing `/` are restored after the normalization,
  because a trailing slash marks a directory in StepUp,
  e.g. a destination directory passed to `make_path_out()`.

- The `render-jinja` feature is now a standalone Python console script, `sc-render-jinja`
  instead of a `stepup` subcommand (tool).
  Steps created by [`render_jinja()`][stepup.core.api.render_jinja] now run `sc-render-jinja ...`
  instead of `stepup render-jinja ...`.
  This matches the recommended pattern for extensions that do not need low-level access to
  StepUp internals.

- The helper function `stepup.core.render_jinja.render_jinja()` is replaced by two functions:
  `render_jinja_file()` renders a template file
  and `render_jinja_str()` renders a template string.
  The `latex` argument became keyword-only,
  and the `str_in` argument is no longer needed
  because `render_jinja_str()` takes the template as its first argument,
  with an optional `name` for error messages.
  (The `render_jinja()` function in `stepup.core.api` is unaffected.)

- Changes that matter when importing from `stepup.core`,
  e.g. in an extension package, a custom tool or a `plan.py`:

    - New `stepup.core.path` module with the path utilities used throughout StepUp,
      including the `StrPath` type alias that appears in all public signatures.
      See [stepup.core.path](reference/stepup.core.path.md) for the full reference.
    - The grab bag in `stepup.core.utils` is reduced to what is genuinely generic.
      Digest formatting moved to `stepup.core.hash`,
      where `format_digest` became `fmt_short_digest`, joined by `fmt_full_digest`.
      Local executable formatting moved to `stepup.core.path`,
      where `format_command` became `format_local_executable`.
      The path helpers (`mynormpath`, `myrelpath`, `translate`, ...)
      moved to `stepup.core.path` as well.
      What stays is renamed to say what it does:
      `string_to_bool` became `to_bool` and `string_to_list` became `as_list`.
      New helpers live in the module they belong to rather than in the grab bag:
      `escape_control_chars` and `format_subprocess` in `stepup.core.utils`,
      `init_joblog` and `append_joblog_record` for the `--joblog` records
      in `stepup.core.job`,
      and the argparse converters `positive_int` and `positive_decimal`
      in `stepup.core.tool`.
    - The pytest helpers in `stepup.core.pytest` have been extended.
      Next to `run_example`, the module now also provides `run_plan`,
      which runs a `plan.py` as an ordinary Python script to check that it does not raise,
      and `ConventionTests`, a base class whose tests check the `__all__` conventions
      for every top-level module of a package in the `stepup` namespace.
      The shell boilerplate of the integration examples was factored out into
      `tests/examples/example.rc`, which also defines the return code bits by name,
      so extension packages can source it in their own examples.
    - The argument of `get_rpc_client()` is renamed from `socket` to `path`.
    - The `STEPUP_STEP_I` environment variable has been replaced by `STEPUP_JOB_I`,
      whose value is also returned by the new `get_job_i()` function in `stepup.core.api`.
      Instead of a step's (stable) node index, it holds a unique id for the current
      job running the step, assigned by the scheduler when the job is created, so a
      deferred step's earlier attempt cannot be confused with its later one.
    - The order of the `StepInfo` attributes is made consistent with the `step()` API function.
    - Several concepts were renamed, which is also visible in the graph output:
      Runner became Builder, Cascade became Trellis,
      Supplier became Source, Consumer became Sink,
      and orphan became detached, with a consistent distinction between
      "detach" (the verb, a state change) and "detached" (the state).
      An amended input, output or environment variable is now called a dynamic one,
      as opposed to an initial one declared by the plan.
      The `amend()` function keeps its name,
      because it is still the call that adds a dependency while the step runs.
    - The *run phase* has been renamed to *build phase*
      throughout the documentation and source code.

- Tools no longer return a return code:
  the signature of `ToolFunc` is now `Callable[[argparse.Namespace], None]`.
  A tool raises `ToolError` to report a mistake the user can fix,
  and calls `sys.exit` when it needs a return code of its own, as `stepup build` does.
  The alias has moved from `stepup.core.utils` to the new `stepup.core.tool` module,
  which collects what the subcommands have in common.
  See [Custom Tools](extending/tool.md) for how to write one.

- A tool entry point in the `stepup.tools` group is called with two arguments,
  `(subparsers, loader)`, instead of only the subparsers.
  The `loader` is a `ConfigLoader` instance,
  which the tool uses to patch its parser with the defaults from the config files.
  The functions registered as entry points are named `add_<name>_subcommand` by convention,
  instead of `<name>_subcommand`.
  See [Custom Tools](extending/tool.md) for a complete example.

### Deprecated

- The `stepup boot` command has been deprecated in favor of `sb` or alternatively `stepup build`.

- The script interface for calling user Python scripts from `plan.py` has been deprecated
  in favor of the new [Call](getting_started/call.md) interface.
  You are encouraged to migrate your `plan.py` files to the new API.

### Removed

#### Command Line and Configuration

- `--show-perf` has been removed.
  Per-step usage information is stored in the workflow database instead
  and can be viewed with `stepup browse`.

- The `STEPUP_SHOW_PERF` environment variable is gone together with the `--show-perf` option.
  (It was not renamed to `STEPUP_BUILD_SHOW_PERF`.)

- The `--root` option of the `stepup` command has been removed.
  Use the `STEPUP_ROOT` environment variable to work on a project
  from outside its root directory.

#### Scheduling

- The `pool` feature has been removed,
  replaced by the more powerful `resources` feature.
  See [Resources](advanced_topics/resources.md)
  and [Resource Constraints](migration/from_3x_to_40.md#resource-constraints-replacement-for-pools-and-blocked-steps)
  in the migration guide for details.

#### Workflow API

- The `${inp}` and `${out}` placeholders have been removed from the `run()` and `step()` functions.
  Use the `shq()` helper function instead, together with Python's built-in f-strings.

- The `glob()` function no longer accepts `_defer` and `_required` keyword arguments.

- Removed the environment variable substitution in the executable passed to `script()` and `call()`.

- The `block=True` argument of `step()` and all step-generating API functions has been removed.
  A step is blocked by requiring a resource that the host does not have,
  e.g. `resources="gate"`, which keeps it pending for the whole build.
  See [Blocked Steps](advanced_topics/blocked_steps.md) for details.

#### File Tracking and the Workflow Graph

- StepUp no longer tracks directories.
  They are either assumed to be present (for static files)
  or created transparently right before a step needs it as a workdir or writes an output into them.
  This has some consequences:

    - The `mkdir()` command has been removed.
    - Input and output files can no longer be directories.

    Some of the internal logic that relied on directories being tracked
    has been refactored to work without them:

    - The watcher uses some simple heuristics to determine which directories to watch.
      It also handles renaming and moving of directories.
    - The cleanup script (`stepup clean`) and the automatic cleanup at the end of a successful run
      will remove empty directories after having removed outdated output files they contained.
    - StepUp now limits its insistence on path affixes (like trailing slashes)
      to only those cases where it is absolutely necessary to avoid ambiguity.

- Cross-pattern named-glob consistency (matching several patterns jointly, e.g.
  `glob("feedback_${*idx}.md", "report_${*idx}.pdf")`) is no longer supported.
  It was rarely, if ever, used in practice, and its removal significantly simplifies
  `stepup.core.nglob` and every module that consumes it.
  As a result, `glob()` and `StepInfo.filter_inp()`/`filter_out()`/`filter_vol()` take a
  single pattern instead of `*patterns`.
  `NGlobMulti` is removed; `NamedGlob` (unchanged for single-pattern use, and now with
  the convenience methods `NGlobMulti` used to provide) is the only named-glob class.
  It was previously named `NGlobSingle`, a name that only made sense next to a "multi"
  counterpart; `NGlobMatch` is likewise renamed to `NamedGlobMatch`.
  Consistency *within* one pattern (the same `${*name}` reused twice in a single pattern
  string) is unaffected.

#### Extensions and Internals

- The `stepup act` subcommand and the `stepup.actions` entry point group have been removed,
  together with the action abstraction layer they exposed.
  An extension that used to register an action now installs a console script instead,
  as described in [Console Scripts](extending/console_scripts.md).

- The `stepup.core.worker` module is gone with the worker processes it implemented,
  including the `WorkThread` object that was handed to every action function.
  An extension that used its `runsh()` and related methods
  can call `run_subprocess()` from `stepup.core.extapi` instead,
  which also records the invocation for later inspection.

- The per-worker log files, `.stepup/worker0.log`, `.stepup/worker1.log` and so on,
  are no longer written, because there are no worker processes to log.
  What a step wrote to standard output and standard error is stored in the workflow database
  and can be inspected with `stepup browse`,
  while the director writes everything else to `.stepup/director.log`.

### Fixed

The redesign of StepUp 4 also removed a long tail of latent bugs,
most of which were rarely or never observed in StepUp 3:
race conditions between the director and its steps,
graph inconsistencies left behind by an interrupted build,
and edge cases in the remote procedure calls between the director and the steps.
These are not listed individually,
because they are entangled with the redesign of the components in which they were found.
An entire class of them was ruled out by strict database session
and transaction management, which keeps the workflow database consistent
when several parts of StepUp write to it at the same time.
The others surfaced because the test suite grew considerably:
more than four times as many unit tests and over 1.5 times as many integration examples.

#### Build Execution and Process Control

- `Ctrl-C` and `SIGTERM` now abort the build in an orderly fashion.
  The director interrupts all running steps with `SIGINT`,
  kills whatever is still running after a few seconds with `SIGKILL`,
  and only then exits, after writing its logs and final report.
  Previously, the terminal user interface exited immediately,
  which cut the director's shutdown short.

- Sending `SIGTERM` to StepUp no longer leaves running steps behind as orphaned processes.

- The third `q` key press kills running steps with `SIGKILL` again, as documented.
  It had escalated to `SIGTERM` instead since version 3.0.0.

- The terminal user interface cleanly exits when the director process fails to start unexpectedly.

- Starting a build no longer refuses to run just because a previous director's socket file
  is still on disk after the process that created it was killed.
  The check now asks the operating system whether the pid advertised in `.stepup/director.log`
  is still alive, and only refuses when it is (or when the pid cannot be determined).

- A keystroke whose command fails inside the director (e.g. `g` when `graph.txt` cannot be
  written) is now reported as an error, and the build carries on.
  Previously this ended `stepup build` with a traceback and discarded the director's return code.

- Pressing `Ctrl-Z` now suspends the whole build, including the running steps.
  Previously they kept running, and writing files, while StepUp itself was stopped.
  The director stops them with `SIGSTOP` and continues them on resume,
  and the time spent suspended is no longer recorded as time a step spent working.

- Resuming StepUp with `fg` no longer leaves a broken terminal:
  the cursor stays visible while the build is suspended,
  and keyboard interaction keeps working after the build is resumed.
  Previously every keystroke was echoed and then swallowed by the terminal.

#### Workflow API

- A known race condition related to `amend(inp=...)` has been fixed.
  It is now safe to call `amend(inp=...)` after a dynamic input file has already been read.
  (It is not the most efficient approach to call `amend(inp=...)` too late,
  but in some cases it is the only practical one.)

#### File Tracking and the Workflow Graph

- Previously computed file hashes of static files are now reused instead of recomputing them.

- A named wildcard (`${*name}`) now matches the same paths as the anonymous `*` it replaces.
  Previously, `glob("data/${*name}")` silently skipped directory matches,
  while `glob("data/*")` included them.
  Consequently, a named wildcard directly following a separator
  no longer matches an empty string, just like `*` in that position.
  The trailing separator of a matched directory is not part of the captured substring.

- Attempts to use files under `.stepup/` in a workflow will now raise an exception.

- A step whose input is changed or deleted while the step is temporarily detached
  from the workflow now runs again once it is recycled.
  Previously it was recycled in its succeeded state and silently kept its stale output.
  This could be observed after an incomplete build (or one run with `--no-clean`),
  which leaves detached steps in the graph for the next build to pick up.

- Fix a bug that could permanently orphan an output file
  and leave all the steps consuming it pending.
  When a step declared a file as its input while the step producing that file was detached,
  e.g. because the plan declaring the producer had not been rerun yet,
  the file was taken away from its producer instead of being left alone.
  Supplying a file to a step no longer changes which step declares that file.

#### Terminal Output and Inspection

- The pages of `stepup browse` escape the labels of the nodes,
  so a command containing `<`, `>` or `&` no longer breaks the layout of the page.

- The progress bar now excludes optional (not required) steps
  correctly from the total count of steps to be executed.

- Running with `--log-level=ERROR` or `--log-level=CRITICAL` no longer ends every successful
  build with a spurious `Errors logged in .stepup/director.log` warning.

#### Extensions and Internals

- The RPC receive loops no longer leave a pending task behind
  when the connection to the other end is closed.
  Such a task ended up in the director log as `Task was destroyed but it is pending!`,
  which is reported as an internal problem at the end of a build.

## [3.2.3][] - 2026-04-16 {: #v3.2.3 }

Bugfix release: support large inodes in SQLite storage

### Fixed

- Fixed a bug in the representation of large inodes in SQLite storage.
  SQLite works with signed 64-bit integers, but inodes can be unsigned 64-bit integers.
  They are now converted back and forth to fit transparently,
  by wrapping too large numbers around to negative values.
  This change is backward compatible.

## [3.2.2][] - 2026-02-08 {: #v3.2.3 }

Minor bugfix and support for profiling with Yappi.

### Added

- Add option to profile the directory process with [Yappi](https://github.com/sumerc/yappi).
- Report timings at the end of the worker log files.

### Fixes

- Fix a queueing bug that caused some steps to remaining pending
  when they should have been executed.

## [3.2.1][] - 2026-01-02 {: #v3.2.1 }

Minor improvements and bugfix.

### Changed

- `stepup browse` shows more details of steps.
- Improve logging of worker processes and Python scripts executed with the `runpy` action.

### Fixes

- Fix filtering of automatically detected dependencies when paths differ due to symlinks.

## [3.2.0][] - 2025-12-28 {: #v3.2.0 }

Improved scheduling of steps with amended inputs and safer `stepup clean` implementation.

### Changed

- Safer and more versatile `stepup clean` implementation:
    - By default, no files are removed. Use the `--commit` option to actually remove files.
    - The standard output consists of bash commands, which can be inspected, grepped
      and/or executed in a terminal to remove the files.
    - Unless the `--all` option is used, only detached files are removed.
      (These are outputs of old steps that are no longer part of the workflow.
      StepUp cleans these up automatically unless you run `stepup boot --no-clean`.)
    - By default, modified output files were never removed.
      Use the `--unsafe` option to override this safety mechanism.
- Improved correctness and efficiency of scheduling of steps with amended inputs.
  This change reduces unnecessary re-execution of steps in some scenarios.
  The implementation requires a database schema version increase,
  meaning that the workflow will be completely rebuilt after an upgrade to this version.

### Fixed

- Fix list of incomplete requirements when steps remain pending.
- Fixed returncode of `stepup act` and some other `stepup` subcommands.

## [3.1.4][] - 2025-12-04 {: #v3.1.4 }

Minor bugfix release.

### Fixed

- Fix a bug in the consistency checks upon startup that (rarely) resulted in false positives.
  This bug was more likely to be triggered with the `--no-clean` option.

## [3.1.3][] - 2025-11-27 {: #v3.1.3 }

### Changed

- All command-line options of `stepup boot` now also have a corresponding environment variable.
- More systematic command-line options for the `stepup boot` command.
  All boolean options now have both a positive and a negative form,
  e.g. `--watch` and `--no-watch`.

## [3.1.2][] - 2025-11-09 {: #v3.1.2 }

Tested with Python 3.14 and small performance improvement

### Added

- Tested with Python 3.14.

### Changed

- The scheduler of StepUp uses job priorities to defer rescheduled jobs
  until all non-rescheduled jobs have been started.
  This lowers the chance that it will be executed again to discover more missing dependencies.

### Fixed

- In worker processes, catch `SystemExit` exceptions from action functions
  to set the return code of the action appropriately.
  This avoids confusing tracebacks when an action calls `sys.exit()`.

## [3.1.1][] - 2025-09-30 {: #v3.1.1 }

Minor bugfix release and a basic logo.

### Added

- A basic logo for the documentation and the graph browser.

### Fixed

- Exclude irrelevant files from Python package.
- Skip dynamically created modules with an ad hoc filename as their `__file__` attribute,
  when tracking local imports of `runpy` actions.

## [3.1.0][] - 2025-09-28 {: #v3.1.0 }

Graph browser (`stepup browse`) and improved `loadns()` function.

### Added

- The `stepup browse` command visualizes the graph in a web browser.

### Changed

- Improved handling of variables in `loadns()` function:
    - Keep trailing slashes in directory paths.
    - Skip variables starting with `_`.

## [3.0.9][] - 2025-09-19 {: #v3.0.9 }

This minor release with an improve `loadns()` function

### Changed

- The `loadns()` function now also accepts path arguments containing environment variables.

## [3.0.8][] - 2025-09-16 {: #v3.0.8 }

This minor release primarily fixes some testing issues.

### Changed

- Drop amend cache manually at the start of a new step.
  This avoids cache errors when rerunning the same step on the same worker process.

## [3.0.7][] - 2025-09-16 {: #v3.0.7 }

This minor release restores compatibility with older SQLite versions (<3.44.0).

### Changed

- Replace the `CONCAT` command by the `||` operator in SQL queries
  to restore compatibility with SQLite versions older than 3.44.0.

## [3.0.6][] - 2025-08-27 {: #v3.0.6 }

This minor release improves the performance of the amend API.

### Changed

- Improved performance of the amend API by reducing the number of calls.

## [3.0.5][] - 2025-08-25 {: #v3.0.5 }

This is a minor bugfix release.

### Fixed

- Fix optional script bug.
  The script interface creates at least two steps: a plan and one or more run steps.
  When the `optional=True` option is used, the plan step must be mandatory,
  which was not the case.
  With this fix, StepUp can decide which optional run steps need to be executed.
- Use StepUp's `getenv` to access the `STEPUP_PATH_FILTER` variable,
  so steps relying on it are re-executed when the variable is updated.

## [3.0.4][] - 2025-06-25 {: #v3.0.4 }

### Fixed

- Minor: when a step failed and its action contained options,
  the command in the output did not work in the terminal. This has been fixed.
- Minor: improve error messages when file permissions or shebangs are incorrect.

## [3.0.3][] - 2025-05-18 {: #v3.0.3 }

### Fixed

- Make `stepup boot` work on macOS, albeit without the `--watch` option.
  (The `--watch` option is implemented using the `asyncinotify` library, which is Linux only.)

## [3.0.2][] - 2025-05-18 {: #v3.0.2 }

Improved return code and a bugfix.

### Changed

- The meaning of the `stepup` return codes has changed to a combination of flags:

    - `1` = internal error (Python exception)
    - `2` = at least one step failed
    - `4` = at least one step remained pending
    - `8` = at least one step was still runnable

  Some sums of return codes are possible.
  For example `6` means that at least one step failed and at least one step remained pending.

## Fixed

- Steps were not made pending when their inputs were created by a new step after a restart.
  This is fixed.

## [3.0.1][] - 2025-05-13 {: #v3.0.1 }

Minor tweaks, improved progress format and `STEPUP_STEP_INP_DIGEST` environment variable.

### Added

- The `STEPUP_STEP_INP_DIGEST` environment variable is set in the worker processes to
  the hex-formatted digest of the inputs of the step.

### Changed

- Improved timer format of running steps in progress bar.

### Fixed

- Minor documentation and configuration fixes.

## [3.0.0][] - 2025-05-11 {: #v3.0.0 }

Major release with breaking changes.
Highlights: custom entry points for the `stepup` subcommands and executable actions,
new/migrated API functions (`loadns()`, `runpy()`, `render_jinja()`),
improved interactions with StepUp running in the background,
and improved terminal user interface.

### Added

- Option `stepup --no-progress` to disable progress information.
  This is sometimes useful when running `stepup` in a non-interactive environment.
- A new API function [`loadns()`][stepup.core.api.loadns] to load variables from file.
  Supported file formats are: JSON, Python, YAML, and TOML.
  This will automatically amend the calling step with the loaded files as inputs.
- The `runpy()` function can now be used to schedule a Python script.
  This automatically amends locall imported modules as inputs to the step.
- The `render-jinja` feature from StepUp RepRep 2 has been migrated to StepUp Core 3.

### Changed

- Breaking:
    - The environment variable `${STEPUP_EXTERNAL_SOURCES}` has been replaced
      by the more versatile `${STEPUP_PATH_FILTER}`.
    - The database schema was incremented because steps now execute "actions",
      which can be shell commands in subprocesses, but also other things,
      such as executing a Python script without starting a new process.
    - While the schema was incremented,
      a small changes was made to the step hash computation.
    - The function [step()][stepup.core.api.step] now accepts a new argument `action`
      instead of a shell command.
      The syntax of an `action` is similar to a shell command:
      It consists of `module.submodule.function arg1 arg2 ...`.
    - `runsh()` mimics the behavior of the old `step()` function.
    - The `stepup` command now uses subcommands to run different tools within StepUp.
    The following tools have been implemented:
        - `stepup act`: Execute an action, mostly for debugging.
        - `stepup boot`: Equivalent to just `stepup` in StepUp 2.
        - `stepup clean`: Equivalent to `cleanup` in StepUp 2.`
        - `stepup drain`: No new steps are started, but running steps are allowed to finish.
        - `stepup join`: Wait for the runner to complete all steps and then shut down StepUp.
        - `stepup graph`: Write out the current graph of a running StepUp instance.
        - `stepup shutdown`: Stop the director process. Repeate to kill running steps.
        - `stepup status`: Print the status of the director process.
        - `stepup wait`: Wait for the runner to complete all steps.
        - `stepup watch-update`: Wait until the watcher observe a file update.
        - `stepup watch-delete`: Wait until the watcher observe a file deletion.
    - The `stepup.core.interact` module now implements several subcommands
      and is no longer inteded to be used directly in Python scripts.
      The old `graph()` function in this modules is now implemented in `stepup.core.api`.
- Internals:
    - Improved type hints in the code.
    - The environment variable `STEPUP_STEP_KEY` (string)
      has been replaced by `STEPUP_STEP_I` (integer).
    - Simplify `Runner.send_to_worker()`.
    - Simplify Job classes.
    - Various minor cleanups.

### Removed

- The `stepup` command no longer accepts an argument to specify an alternative for `plan.py`.

## [2.1.7][] - 2025-04-24 {: #v2.1.7 }

Minor enhancements and bugfixes.

### Added

- Print progress information on every line when stdout is not a terminal.
- The `stepup` command now accepts the `--no-clean` option
  to disable removal of outdated outputs at the end of a successful run.

### Changed

- Simplified the output of the `cases` command of the script [`driver()`][stepup.core.script.driver].
- The arguments `inp`, `out` and `vol` are converted to `Path` instances
  before calling the `run()` function.

### Fixed

- Never amend `HERE` and `ROOT` environment variables.

## [2.1.6][] - 2025-04-24 {: #v2.1.6 }

This is a minor bugfix release.

### Fixed

- Do not abort StepUp when wal or shm files are present.
- Upon restart, handle removed files correctly that previously matched a deferred glob.

## [2.1.5][] - 2025-03-25 {: #v2.1.5 }

This is a minor bugfix release.

### Fixed

- Fixed bug in format string in `stepup.core.api`.
- Small cleanups
- Tweak absolute path tests for non-FHS systems.

## [2.1.4][] - 2025-02-12 {: #v2.1.4 }

This is a minor bugfix release.

### Fixed

- Fix a bug when using `getenv(..., multi=True)` with a non-existing environment variable.

## [2.1.3][] - 2025-02-12 {: #v2.1.3 }

This is a minor bugfix release.

### Fixed

- Fix a bug related to input validation of steps with amended inputs.

## [2.1.2][] - 2025-02-12 {: #v2.1.2 }

This is a minor bugfix release.

### Fixed

- Fix an RPC timeout bug.

## [2.1.1][] - 2025-02-12 {: #v2.1.1 }

This is a minor bugfix release.

### Fixed

- Disable input checking when running a `ValidateAmendJob`.
  (It is expected that inputs may not be consistent yet at this stage.)
  This eliminates some false positive input errors.

## [2.1.0][] - 2025-02-12 {: #v2.1.0 }

This release improves the overall robustness of StepUp.
Most importantly, table constraints are introduced on the `file` table in `.stepup/graph.db`,
eliminating potential bugs by design (or making them easier to fix).
The constraints change the database schema,
so `graph.db` files created with version 2.0 will be discarded.
The workflow will be completely rebuilt after an upgrade to StepUp Core 2.1.

This release also refactors the implementation of file and step hashes, and worker processes.
Finally, error messages and exception handling have been improved.

### Added

- The log level can be controlled with the `STEPUP_LOG_LEVEL` environment variable.
  Alternatively, set `STEPUP_DEBUG=1`, which will also activate additional debugging output.
  (This replaces the former `STEPUP_STRICT` environment variable.)
- Improve handling of unexpected file changes.
  Before a step is executed or skipped, and after it has completed,
  changes to inputs (since they were declared static or built by previous steps),
  will cause the step to fail and the scheduler to drain.
  (This feature requires a database schema version increase.)

### Changed

- Because of other database schema changes in this release,
  also the `FileState` enumeration was relabeled in a more chronological order.
- The `cleanup` command always runs in the most verbose mode (`-v` no longer supported).
  It now also supports the `-d` or `--dry-run` option to show which files would be cleaned.
- The variable `${STEPUP_EXTERNAL_SOURCES}` can now also contain relative paths,
  which are assumed to be relative to `${STEPUP_ROOT}`.
- The default timeout for RPC calls has been increased from 5 to 300 seconds.
  It can be controlled with the `STEPUP_SYNC_RPC_TIMEOUT` environment variable.
  Setting it to a negative value will disable the timeout
  and make RPC calls wait indefinitely for a response.

### Fixed

- Table constraints are introduced to ensure file states and hashes are consistent.
  This eliminates some difficult to reproduce bugs or makes them easier to fix.
  (This change requires a database schema version increase.)
- Code documentation updates and internal cleanups.
- Renaming and moving directories in watch phase is now handled correctly.
- Fixed routine to wipe database in case of a schema version change.
- Add safety check to prevent two StepUp instances from running in the same directory.
- Add a warning when errors are reported in `.stepup/director.log`.
- When running StepUp with the `-w` option and the scheduler is drained,
  queued steps are now made pending again, ensuring they are only executed when appropriate.

## [2.0.7][] - 2025-02-06 {: #v2.0.7 }

This release fixes two recursive glob issues.

### Fixed

- Fixed issues with directories matching `glob("...", _defer=True)`,
  which are later used as parent directories in various scenarios.
- Fix bug in recursive glob to match `data/inp.txt` with the pattern `data/**/inp.txt`

## [2.0.6][] - 2025-02-05 {: #v2.0.6 }

This release introduces the `STEPUP_EXTERNAL_SOURCES` environment variable
for more fine-grained control over automatic dependency tracking.

### Added

- The `STEPUP_EXTERNAL_SOURCES` environment variable can be set to
  a colon-separated list of directories with source files outside `STEPUP_ROOT`.
  The `script` and `call` drivers use this to decide which imported Python modules
  to consider as inputs to a step.

### Changed

- Switch from [SemVer](https://semver.org/spec/v2.0.0.html) to
  [EffVer](https://jacobtomlinson.dev/effver/).

## [2.0.5][] - 2025-01-28 {: #v2.0.5 }

This is a minor release, just adding a utility function.

### Changed

- Use `string_to_bool` to interpret the environment variable `STEPUP_STRICT`.
  E.g., setting it to `"0"` will disable strict mode.

## [2.0.4][] - 2025-01-28 {: #v2.0.4 }

This release fixes very minor issues. It is mainly for testing release automation.

### Fixed

- Use `importlib.metadata` instead of `_version.py` to get the version number.
- Add `--version` option to `stepup` command.
- Improve screen output consistency.

## [2.0.3][] - 2025-01-27 {: #v2.0.3 }

This release fixes one pesky bug.

### Fixed

- It was previously not possible to reattach a detached step to a different creator
  when this step was not a top-level detached node.
  This limitation has been lifted, because it is a fully legitimate use case.

## [2.0.2][] - 2025-01-25 {: #v2.0.2 }

This release fixes several bugs.

### Added

- Environment variable `STEPUP_STRICT` to enforce strict mode.
  This disables automatic fixes in the database that can only be caused by bugs.

### Fixed

- A bug is fixed in the logic to determine the type of job to run for a given step.
  Some steps were executed while not all required inputs were present.
- A bug is fixed that caused optional steps not to be executed again, when their inputs
  had changed or their outputs were removed.
- A bug is fixed that caused outputs of steps to be removed when they were changed
  from `optional=False` to `optional=True`.
- Occasionally, `.stepup/` was not created yet
  when the reporter tried writing to `.stepup/success.log`.
- When multiple steps were changed and StepUp is restarted,
  steps created by a by another modified step were executed before the creating step.
  This is fixed.
- Fix a few issues found by deepsource.io.

## [2.0.1][] - 2025-01-22 {: #v2.0.1 }

(Version 2.0.0 was yanked due to a packaging issue.)

### Added

- New option `-W` or `--watch-first` to automatically rerun steps after a file has changed.
- Press `q` a second time to kill running steps with SIGINT, similar to ctrl-c.
- Press `q` a third time to kill running steps with SIGKILL, nuclear option.
- `stepup` has a meaningful returncode:
    - `0` = all mandatory steps succeeded
    - `1` = internal error (Python exception)
    - `2` = at least one step failed
    - `3` = no steps failed, but some remained pending
- Failed steps (if any) are also logged to `.stepup/fail.log`,
  which is more convenient to inspect than scrolling back in the terminal.
  Similarly, all warnings (if any) are written to `.stepup/warning.log`.
- `--perf` option to analyze performance bottlenecks in the director process.
- The "call" protocol is added as a light alternative to the "script" protocol.
  It can be used through the new [`call()`][stepup.core.api.call] function.
- `getinfo()` function to retrieve the
  [`StepInfo`][stepup.core.stepinfo.StepInfo] object of the current step.
- Cleanly exit the director process upon several types of exceptions (instead of hanging).
- Gracefully handle `SIGINT` and `SIGTERM`, e.g. pressing `ctrl-c` in the terminal.

### Changed

- Breaking changes to `stepup.core.api`:
    - The [`getenv()`][stepup.core.api.getenv] function has been extended and now has three options
      (`path`, `rebase` and `multi`) to control how the environment variable gets processed.
    - The optional `workdir` argument of the [`script()`][stepup.core.api.script] function
      must always be specified as a keyword argument.
    - The `block` argument of the [`plan()`][stepup.core.api.plan] function
    must be given as a keyword argument.
    - All optional arguments of [`copy()`][stepup.core.api.copy]
      and `mkdir()` must be given as keyword arguments.
    - `plan.py` scripts must start with `#!/usr/bin/env python3` instead of `#!/usr/bin/env python`.
    - The [`amend()`][stepup.core.api.amend] function now raises an exception when the
      amended inputs are not available yet, instead of returning `False`.

- Backward compatible changes to `stepup.core.api`:
    - The [`script()`][stepup.core.api.script] function has an extra `step_info` option
      to specify a file to which the `step_info` objects of the run part(s) is/are written.
      This comes with an extension of the script protocol: `./script.py plan` must
      accept an optional argument `--step-info=...`
    - The [`script()`][stepup.core.api.script] function now accepts all arguments
      that can be passed on to the underlying [`step()`][stepup.core.api.step] call.
      There are only relevant for the plan stage of the script protocol.
    - The script `driver()` now detects local imports in the
      `run()` function of the script and amends them as inputs.
    - The [`plan()`][stepup.core.api.plan] function now accepts all arguments
      that can be passed on to the underlying [`step()`][stepup.core.api.step] call.

- Command-line and terminal interface changes:
    - By default, StepUp will exit after having executed all runnable steps.
      Use the option `-w` or `--watch` to keep `stepup` running and watching for file changes.
    - Keyboard interaction works with and without the (new) `--watch` option.
    - The `cleanup` script now also works when `stepup` is not running.
      It also features an improved verbosity option.

- Terminology changes:
    - The "source ➜ sink" graph is now called the dependency graph.
    - The "creator ➜ product" graph is now called the provenance graph.

- Internal changes:
    - Complete refactoring of the internal workflow data structure, file format and the core algorithms.
      For example, if some files change, StepUp can better narrow down which steps are worth rerunning.
    - The workflow is now entirely stored in an SQLite database, in `.stepup/graph.db`,
      which has major benefits:
        - When an RPC call modifies the workflow and causes an exception,
          the workflow rolls back to its last known valid state (before the RPC call),
          thanks to SQLite's [ACID properties](https://en.wikipedia.org/wiki/ACID).
          This eliminates many potential bugs by construction.
        - Upon restart, StepUp can continue without noticeable delay where it last stopped,
          because its entire last-known state of the workflow is readily available.
          StepUp only needs to check for changed files and environment variables
          to decide if (additional) steps need to be made pending.
        - If something goes wrong unexpectedly in a complex production workflow,
          the `graph.db` file can be inspected with `sqlitebrowser` to debug the issue
          and potentially derive a small test case to be added to the unit tests.
      The use of SQLite adds a (small) computational overhead
      compared to storing the same information in native Python data structures.
      This release has not been extensively optimized for performance.
    - Improved tracking of file changes.
      Unexpected changes to input files of steps in the run phase will cause an exception.

### Removed

- StepUp no longer uses `msgpack` and uses pickling for serialization instead.
  The `msgpack` dependency has been removed.
  Related `structure()` and `unstructure()` methods have been removed.
- The `-f` or `--workflow` argument of the director server has been removed.
- The `f` (from scratch) and `t` (try replay) keys have been removed
  from the terminal user interface.

### Fixed

- When static file has been deleted (missing) and later restored,
  the restored file was not noticed when restarting StepUp. This is fixed.
- Tests have been made compatible with Python 3.13.
- Files with whitespace are handled correctly.
  (That being said, we don't recommend using files with whitespace.)

## [1.3.1][] - 2024-09-17 {: #v1.3.1 }

### Fixed

- Fix incorrect parsing of `?*` and `*?` wildcards in the `nglob` module.

## [1.3.0][] - 2024-08-27 {: #v1.3.0 }

### Added

- Add support for standard output and error redirection in the script driver.
  The dictionary returned by the `info()` or `case_info()` functions
  can include `"stdout"` and/or `"stderr"` items.
  The values of these two fields are paths to which the standard output and/or error
  of the run part of the script are redirected.
- All API functions that define a step now return a `StepInfo` instance,
  which may contain useful information (e.g. output paths) to define follow-up steps.
  This is mainly useful for API extensions that define higher-level functions to create steps,
  e.g. as in [StepUp RepRep](https://reproducible-reporting.github.io/stepup-reprep/).
- The classes `NGlobMulti` has a new method `single()`
  and `NGlobMatch` has a new property `single`.
  These are only valid when there is a unique match,
  i.e. when the `files()` method or property has exactly one path.

### Changed

- Migrate `load_module_file` to stepup-reprep.
- Replace [watchdog](https://github.com/gorakhargosh/watchdog)
  by [asyncinotify](https://github.com/ProCern/asyncinotify)
  to avoid [a long-standing issue in watchdog](https://github.com/gorakhargosh/watchdog/issues/275).
- :warning: **API-breaking** :warning:
  When a step is defined with a working directory different from `'./'`,
  relative paths provided in other arguments to the `step()` function
  are interpreted relative to the given working directory,
  not the current working directory of the running process.
- The directory `.stepup` is no longer created when running `stepup`
  without a `plan.py`.
- The files in `.stepup/logs` have been renamed to `*.log` files under `.stepup`.

### Fixed

- Fix bug in the translation of relative paths before they are sent to the director process.
- Add trailing slash to `workdir` argument of `stepup.core.api.step()` if it is missing.
- Fix mistake in worker log filenames.
- Fix bug in back translation of paths when substituted in a step command.
- Improve compatibility of nglob with Python's built-in glob.

## [1.2.8][] - 2024-06-28 {: #v1.2.8 }

### Fixed

- Modify the script driver so that `info()` and `case_info()` may return empty dictionaries.

## [1.2.7][] - 2024-06-24 {: #v1.2.7 }

### Fixed

- Add workaround for Python==3.11 bug with RPC over sockets.
  The RPC server (created with `asyncio.start_unix_server`) closes before all requests are handled.
  A stop event is now included for all RPC handlers
  to wait with stopping the server until every request is handled.
  This is a [known issue fixed in Python 3.12.1](https://github.com/python/cpython/issues/120866).

## [1.2.6][] - 2024-06-13 {: #v1.2.6 }

### Fixed

- Do not watch files when running StepUp non-interactively.
  This makes non-interactive mode a workaround for
  [a nasty watchdog bug](https://github.com/gorakhargosh/watchdog/issues/275).

## [1.2.5][] - 2024-06-13 {: #v1.2.5 }

### Fixed

- Effectively make watching recursive when a directory is added that is known in the workflow.
- The function `amend()` now always returns `True` when the RPC client is a dummy.
  This fixes early exits from scripts that used `amend()` when they are called manually.
- Prevent the `Cannot watch non-existing directory` error by ensuring that deferred glob matches
  exist before they are included as static files in the graph.
- Check that local scripts have a shebang line before trying to execute them.
- Improved continuous integration setup
- Minor documentation improvements
- Minor code cleanups

## [1.2.4][] - 2024-05-27 {: #v1.2.4 }

### Changed

- Include "hidden" files when globbing.

### Fixed

- Do not refuse to replay unchanged step that declares its own static inputs.
- Make recursive glob consistent with Python's built-in glob in `step.core.nglob`.
- Pool definitions are stored in workflow and replayed correctly when a step is skipped.

## [1.2.3][] - 2024-05-19 {: #v1.2.3 }

### Changed

- Completed and revised docstrings in `stepup.core.nglob`,
  and added this module to the reference documentation.

### Fixed

- Improve hash computation of a symbolic links in `stepup.core.hash`.

## [1.2.2][] - 2024-05-16 {: #v1.2.2 }

### Changed

- Documentation updates.

### Fixed

- Make `cleanup` command work in project subdirectories when `STEPUP_ROOT` is set.
- Avoid useless wait when running a `plan.py` script outside of `stepup`.

## [1.2.1][] - 2024-05-07 {: #v1.2.1 }

### Fixed

- Fixed packaging mistake that confused PyCharm and Pytest.

## [1.2.0][] - 2024-05-02 {: #v1.2.0 }

### Added

- Export of graphs to [Graphviz](https://graphviz.org/) DOT files.
- The `cleanup` script for manually cleaning up outputs.

### Changed

- Documentation updates.
- Limit acyclic constraint to the source-sink graph.
  This means a step can declare a static file and then amend it as input.
- Refactoring of the file `stepup.core.watcher` module:
    - Replace dependency `watchfiles` by `watchdog`.
    - Rename functions in `stepup.core.interact`:
        - `watch_add()` -> `watch_update()`
        - `watch_del()` -> `watch_delete()`
    - Separate watcher and runner coroutines with reduced risk for race conditions related to
      `watch_delete()` and `watch_update()` to address `TimeoutError`.
    - Place custom asyncio utilities in `stepup.core.asyncio`.
    - The watcher also tracks changes to static files while steps are being executed.
    - Directories are watched as soon as they are created.
- The function `stepup.core.interact.graph` takes a prefix argument instead of a full filename,
  e.g. `graph` instead of `graph.txt`.

### Fixed

- More graceful error message when the director process crashes early.
- Fix compatibility with [asciinema](https://asciinema.org) terminal recording.
- Raise `ConnectionResetError` in `SocketSyncRPCClient` instead of blocking forever when
  the director process crashes.

## [1.0.0][] - 2024-04-25 {: #v1.0.0 }

Initial release

[Unreleased]: https://github.com/reproducible-reporting/stepup-core
[4.0.0rc14]: https://github.com/reproducible-reporting/stepup-core/releases/tag/v4.0.0rc14
[3.2.3]: https://github.com/reproducible-reporting/stepup-core/releases/tag/v3.2.3
[3.2.2]: https://github.com/reproducible-reporting/stepup-core/releases/tag/v3.2.2
[3.2.1]: https://github.com/reproducible-reporting/stepup-core/releases/tag/v3.2.1
[3.2.0]: https://github.com/reproducible-reporting/stepup-core/releases/tag/v3.2.0
[3.1.4]: https://github.com/reproducible-reporting/stepup-core/releases/tag/v3.1.4
[3.1.3]: https://github.com/reproducible-reporting/stepup-core/releases/tag/v3.1.3
[3.1.2]: https://github.com/reproducible-reporting/stepup-core/releases/tag/v3.1.2
[3.1.1]: https://github.com/reproducible-reporting/stepup-core/releases/tag/v3.1.1
[3.1.0]: https://github.com/reproducible-reporting/stepup-core/releases/tag/v3.1.0
[3.0.9]: https://github.com/reproducible-reporting/stepup-core/releases/tag/v3.0.9
[3.0.8]: https://github.com/reproducible-reporting/stepup-core/releases/tag/v3.0.8
[3.0.7]: https://github.com/reproducible-reporting/stepup-core/releases/tag/v3.0.7
[3.0.6]: https://github.com/reproducible-reporting/stepup-core/releases/tag/v3.0.6
[3.0.5]: https://github.com/reproducible-reporting/stepup-core/releases/tag/v3.0.5
[3.0.4]: https://github.com/reproducible-reporting/stepup-core/releases/tag/v3.0.4
[3.0.3]: https://github.com/reproducible-reporting/stepup-core/releases/tag/v3.0.3
[3.0.2]: https://github.com/reproducible-reporting/stepup-core/releases/tag/v3.0.2
[3.0.1]: https://github.com/reproducible-reporting/stepup-core/releases/tag/v3.0.1
[3.0.0]: https://github.com/reproducible-reporting/stepup-core/releases/tag/v3.0.0
[2.1.7]: https://github.com/reproducible-reporting/stepup-core/releases/tag/v2.1.7
[2.1.6]: https://github.com/reproducible-reporting/stepup-core/releases/tag/v2.1.6
[2.1.5]: https://github.com/reproducible-reporting/stepup-core/releases/tag/v2.1.5
[2.1.4]: https://github.com/reproducible-reporting/stepup-core/releases/tag/v2.1.4
[2.1.3]: https://github.com/reproducible-reporting/stepup-core/releases/tag/v2.1.3
[2.1.2]: https://github.com/reproducible-reporting/stepup-core/releases/tag/v2.1.2
[2.1.1]: https://github.com/reproducible-reporting/stepup-core/releases/tag/v2.1.1
[2.1.0]: https://github.com/reproducible-reporting/stepup-core/releases/tag/v2.1.0
[2.0.7]: https://github.com/reproducible-reporting/stepup-core/releases/tag/v2.0.7
[2.0.6]: https://github.com/reproducible-reporting/stepup-core/releases/tag/v2.0.6
[2.0.5]: https://github.com/reproducible-reporting/stepup-core/releases/tag/v2.0.5
[2.0.4]: https://github.com/reproducible-reporting/stepup-core/releases/tag/v2.0.4
[2.0.3]: https://github.com/reproducible-reporting/stepup-core/releases/tag/v2.0.3
[2.0.2]: https://github.com/reproducible-reporting/stepup-core/releases/tag/v2.0.2
[2.0.1]: https://github.com/reproducible-reporting/stepup-core/releases/tag/v2.0.1
[1.3.1]: https://github.com/reproducible-reporting/stepup-core/releases/tag/v1.3.1
[1.3.0]: https://github.com/reproducible-reporting/stepup-core/releases/tag/v1.3.0
[1.2.8]: https://github.com/reproducible-reporting/stepup-core/releases/tag/v1.2.8
[1.2.7]: https://github.com/reproducible-reporting/stepup-core/releases/tag/v1.2.7
[1.2.6]: https://github.com/reproducible-reporting/stepup-core/releases/tag/v1.2.6
[1.2.5]: https://github.com/reproducible-reporting/stepup-core/releases/tag/v1.2.5
[1.2.4]: https://github.com/reproducible-reporting/stepup-core/releases/tag/v1.2.4
[1.2.3]: https://github.com/reproducible-reporting/stepup-core/releases/tag/v1.2.3
[1.2.2]: https://github.com/reproducible-reporting/stepup-core/releases/tag/v1.2.2
[1.2.1]: https://github.com/reproducible-reporting/stepup-core/releases/tag/v1.2.1
[1.2.0]: https://github.com/reproducible-reporting/stepup-core/releases/tag/v1.2.0
[1.0.0]: https://github.com/reproducible-reporting/stepup-core/releases/tag/v1.0.0
