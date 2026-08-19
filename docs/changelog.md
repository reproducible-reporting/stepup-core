# Changelog
<!--
SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
SPDX-License-Identifier: CC-BY-SA-4.0
-->
<!-- markdownlint-disable no-duplicate-heading -->

All notable changes to StepUp Core will be documented on this page.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Effort-based Versioning](https://jacobtomlinson.dev/effver/).
(Changes to features documented as "experimental" will not increment macro and meso version numbers.)

## [Unreleased][]

## [4.0.0rc10][] - 2026-07-27 {: #v4.0.0rc10 }

This is release candidate 10 of the upcoming StepUp Core 4.0 release.

Note that all changes of the release candidates are included below.
This section is treated as a draft of the changelog for the final 4.0.0 release,
and will be updated with any further changes before the final release.

### Added

- StepUp can now also be configured through configuration files,
  in addition to environment variables and command-line arguments.
  See [Configuration files](reference/configuration.md) for details.
- The `stepup show-config` command shows the current configuration,
  as the result of merging all config files and environment variables.
  It also lists the `STEPUP_*` environment variables in three groups,
  separating the ones it recognizes as settings from the ones used internally
  and from the ones without any effect,
  because the name of an environment variable cannot be checked the way a config key is.
- Mistakes in a config file or a `STEPUP_*` environment variable are reported as a list of
  short messages instead of a Python traceback, and stop the subcommand with return code `1`.
  All problems are listed at once, each naming the file or variable to fix.
  Unknown sections and keys are now also detected, with a suggestion where a key belongs
  or how it is spelled correctly.
  The `stepup show-config` command is the exception that still runs,
  so the configuration can be inspected precisely when it is broken.
  It shows each problem on the line of the setting, section or config file it concerns.
  Problems are shown in red when the terminal supports color.
- `stepup build [targets...]` restricts the build to the steps needed to produce
  the given output files (and their dependencies), instead of the full default workflow.
  A target cannot name a volatile output or a static file, and a target that is never
  produced by any step is reported as a warning at the end of the build.
  Targets may now also name a directory (a path ending in `/`),
  which elevates every step whose declared need is `DEFAULT`
  and whose output falls under that directory, best-effort (never raises).
  Automatic cleaning is disabled when targets are specified.
  See [Build Targets](advanced_topics/build_targets.md) for details.
- StepUp can use a forkserver for Python step execution and file hashing,
  which reduces startup overhead.
  This can be controlled with the `--forkserver` flag,
  which is enabled by default on Linux.
- Added `--preload-modules` option to `sb` to specify a comma-separated list of Python
  modules to be pre-loaded into the forkserver.
  This only has an effect when `--forkserver` is active and can speed up workflows
  that repeatedly import large modules.
- StepUp now stores the captured stdout and stderr of each step in the workflow database,
  so they can be inspected after the build.
  Output from subprocesses launched by a forked Python step is captured properly.
  The amount stored per stream can be capped with the new `STEPUP_MAX_OUTPUT_SIZE`
  environment variable (`0` = unlimited, the default).
  These outputs can later be viewed with `stepup browse`.
- All functions in `stepup.core.api` now accept `os.PathLike` objects (i.e. `pathlib.Path`)
  as path arguments, in addition to `str` and `path.Path`.
- When the first word of a `run()` command is a bare command name matching a `console_scripts`
  entry point from the current Python environment, StepUp now runs it as a Python entry point:
  when the forkserver is enabled (`--forkserver`), the entry point function is called
  in-process rather than spawning a new subprocess, reducing overhead.
  If the entry point belongs to a different Python environment, a warning is logged and
  the command falls back to direct subprocess execution.
- A `run()` or `step()` command may now start with `VAR=value` assignments
  (when `shell=False`), e.g. `run("OMP_NUM_THREADS=4 ./work.py")`.
  These are applied as step-specific environment variable overrides when the step runs,
  which is otherwise impossible without a shell.
  The overrides are part of the step hash, so changing a value reruns the step.
  A variable cannot be both an override and an `env` dependency.
- `step()` accepts a new `duration` argument:
  an initial estimate (in seconds) of the step's wall time,
  used by the scheduler (when `--duration` is enabled) to prioritize execution order
  before any measurement is available.
  All step-generating API functions (`run()`, `script()`, `call()`, `render_jinja()`, etc.)
  also accept a `duration` argument.
- The `command` argument of `step()`, `run()` and `plan()` may now be a callable
  that builds the command from the step's own paths,
  so a path list no longer has to be named twice:

  ```python
  run(lambda out: f"./gen.py {shq(out)}", out=["out1.txt", "out2.txt"])
  ```

  The callable may declare any subset of the parameters `inp`, `out` and `vol`,
  matched by name, and receives the paths after environment variable substitution
  and normalization.
- New `hold()` context manager in `stepup.core.api`, for a step (typically a `plan.py`) to
  wrap a batch of declarations so its children are held back from dispatch until the block
  closes, instead of each being dispatched as soon as it is declared.
  This lets the whole batch become simultaneously eligible and get sorted by `_tail_time`
  once released, so slow children declared late no longer lose the race for job slots to
  fast children declared early. `hold()` is re-entrant: nested `with hold():` blocks for the
  same step (e.g. through a shared helper function) compose correctly, with children staying
  held back until the outermost block exits.
- New `stepup.core.extapi` module for StepUp extension developers,
  collecting utilities previously buried in `stepup.core.utils`.
  See [stepup.core.extapi](reference/stepup.core.extapi.md) for the full reference
  and [Custom API Functions](extending/api.md) for usage guidance.
  A few utilities aimed at extension developers stay in `stepup.core.api`,
  because `stepup.core.extapi` is built on top of it:
  `subs_env_vars`, `get_rpc_client` and `get_job_i`.
- Extension wrapper steps can now record the exact subprocess invocations they make,
  using `run_subprocess` in `stepup.core.extapi`,
  which executes the subprocess and records its invocation.
  Alternatively, `record_subprocess()` can be used to record a subprocess that was already executed,
  e.g. using the built-in `subprocess` module.
  The command line, working directory, environment overlay and return code are stored in a
  new `step_subprocess` table for debugging and archival.
  Recorded invocations are shown in `stepup browse`, formatted as shell-pasteable command lines.
  See [Custom API Functions](extending/api.md) for implementation guidance.
- Added a `--fix-epoch` option to `sb` (on by default)
  to set the `SOURCE_DATE_EPOCH` environment variable to a fixed value for all step executions.
  This is useful for ensuring reproducible builds.
  See [Configuration files](reference/configuration.md) for details.
- A resource usage report is shown ad the end of the file `.stepup/director.log`.
  Part of the analysis relies on Linux control groups, which are only available on this OS.
- An SQL debug log option, to check query plans and execution times.
- Added a `--defer-cap` option (default 100) that fails a step
  once it has been deferred that many times in a row without succeeding.
  This acts as a livelock guard for `amend()`-driven defers.
- Added support for cgroup v2 memory accounting on Linux with `systemd-run`.
- Added a `--joblog` option to `sb` to log the start and end of each job to a file.

### Changed

- Relicense the StepUp Core source code under `LGPL-3.0-or-later`.
  This clarifies that users of StepUp can assign any license of their choice
  to the workflows they create with StepUp (e.g., `plan.py` and related files).
  This has always been the intention, but with this change, it becomes legally explicit.
- `stepup boot` has been renamed to `stepup build`
  and can be called conveniently with the `sb` shortcut.
  The `boot` command will be removed in a future release.
- The `--num-workers` / `-n` option of `sb` has been renamed to `--jobs` / `-j`,
  in line with the convention used by `make` and similar tools.
  The config-file key changes from `num_workers` to `jobs`,
  and the environment variable changes from `STEPUP_NUM_WORKERS` to `STEPUP_BUILD_JOBS`.
  The default value is now `1.0` (one job per CPU core) instead of `1.2`.
  (The old values is common for I/O-bound build workflows,
  but StepUp is more commonly applied to CPU-bound workflows,
  for which the new default is more suitable.)
- The CPU detection (when `-j` is given as a float) has been extended.
  It now tries, in order:
  1. The number of cores available within the current cgroup (cgroup v2 only).
  2. Job-scheduler CPU-related environment variables (SLURM, PBS).
  3. The CPU affinity mask reported by the operating system.
  4. The total number of CPUs reported by the operating system.
  The first source that yields a usable value is used.
- After a step fails, the scheduler is now put on hold by default, like `make` without
  `-k` (steps already running still finish; no new steps are started).
  Use the new `--keep-going` / `-k` flag (or `STEPUP_BUILD_KEEP_GOING`) to restore the
  previous behavior of continuing to build every step whose inputs remain available.
- The end-of-build pending report no longer prints one `PENDING Step` page per pending step.
  Instead, it summarizes the **root causes** as a fixed-size ranked report: the unavailable
  input files and blocked resources that account for the most pending steps, plus a
  count of steps blocked by failed steps, waiting on each other, deferred, or otherwise
  unexplained. Use `stepup browse` to inspect the individual steps behind any entry.
  See [Blocked Steps](advanced_topics/blocked_steps.md) for details on the new format.
- The `static()` and `glob()` functions have been redesigned from scratch to permit more use cases
  while still imposing the same safety and correctness guarantees as in StepUp 3.
  See [`static()` and `glob()` Have New Roles](migration/from_3x_to_40.md#static-and-glob-have-new-roles)
  and [Directory Handling](migration/from_3x_to_40.md#directory-handling)
  in the migration guide for details.
- Return codes have changed.
  The new return code bits are documented in [StepUp Return Codes](reference/returncode.md).
  The changes compared to StepUp 3 are summarized in the [migration guide](migration/from_3x_to_40.md#return-codes-have-been-renumbered).
- When a step fails because of incorrect use of `stepup.core.api`,
  the error is now reported concisely instead of as a long traceback.
  Errors that indicate a bug in StepUp keep their full traceback.
  Run `sb` with the environment variable `STEPUP_DEBUG=1` to see the complete traceback.
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
- The `runsh()` and `runpy()` functions have been replaced by the more flexible `run()` function.
  The new implementation is more efficient and automatically tracks local scripts as dependencies.
- The `plan()` function has been made maximally similar to `run()`,
  and now accepts arbitrary local Python scripts,
  not just a directory that must contain a `plan.py` script.
- Redesigned `call()` interface:
  the old inp/out/pickle argument modes are replaced
  by explicit function dispatch and optional `args_file` support for file-based argument passing.
  See [Function Calls](getting_started/call.md) for details.
- The `getinfo()` function has been renamed to `get_info()`.
- File hashes are computed in concurrent hash jobs instead of the old serial client-side delegation.
  Similarly, the director uses the same mechanism to compute file hashes in parallel on startup.
- File hashing now runs in threads inside the director process
  instead of subprocesses or forkserver children,
  which lowers overhead while remaining promptly interruptible.
  As a result, the "Hashing" row is gone from the resource-usage summary;
  that time is now counted under "Director".
- A known race condition related to `amend(inp=...)` has been fixed.
  It is now safe to call `amend(inp=...)` after a dynamic input file has already been read.
  (It is not the most efficient approach to call `amend(inp=...)` too late,
  but in some cases it is the only practical one.)
- The scheduler has been replaced by a new and more efficient implementation.
  This change also comes with several improved features:
    - Steps are prioritized using the *tail time*, which results in the shortest overall
      execution time of the workflow.
      This is also known as critical path scheduling.
      Since StepUp assumes no full knowledge of the workflow,
      the tail time estimates are updated dynamically as new edges are discovered.
    - A new step that has not been executed before is assigned a duration of 1 second.
      When restarting StepUp, the duration of steps from previous runs is used,
      even if inputs changed, so that the scheduler can make better tail time estimates.
    - The `pool` feature has been removed and
      is now replaced by the more powerful `resources` feature.
    - The `optional` feature has become more robust.
    - The "rescheduling" mechanism has been refactored by a simpler "defer" mechanism.
- The "deferred glob" has been replaced by a simpler "static tree" concept.
  Files in a static tree become static only when they are used as inputs.
  This allows for huge static data directories, of which only some are used,
  without having to glob the entire directory recursively.
  To declare a static tree directory, just pass it as an argument to the `static()` function.
  `static()` will treat all directory arguments are static trees.
  Static trees interact with `static()` and `glob()` as follows:
    - `static()` on a path already covered by a static tree is now a no-op,
      instead of raising an error.
      A static tree must still be declared before any file it contains;
      the reverse order still raises.
    - `glob()` no longer declares file matches already covered by a static tree,
      since the tree already owns them.
      This makes overlapping `glob()` calls over the same static tree work:
      declare the tree once with `static()`, then `glob()` it as often as needed.
    - Declaring a static tree that already contains previously declared files raises an error.
    - A directory match of a `glob()` pattern is only accepted when the
      directory lies inside a static tree.
      Outside a static tree, StepUp has no evidence that the directory is source material
      rather than a step's build product, so the set of matches could depend on build progress.
- `glob()` and `StepInfo.filter_inp()`/`filter_out()`/`filter_vol()` now take a single
  pattern instead of `*patterns`.
- The `render-jinja` feature is now a standalone Python console script, `sc-render-jinja`
  instead of a `stepup` subcommand (tool).
  Steps created by [`render_jinja()`][stepup.core.api.render_jinja] now run `sc-render-jinja ...`
  instead of `stepup render-jinja ...`.
  This matches the recommended pattern for extensions that do not need low-level access to
  StepUp internals.
- Every step now runs in a session of its own,
  so a `Ctrl-C` in the terminal no longer reaches step processes directly.
  The director is the only thing that stops them, on every route.
  As a result, aborting a build now also stops the actual work of a shell step
  that is a pipeline or an `&&`-chain,
  which previously kept running because only its surrounding `sh` was signalled.
- The database schema version has been incremented to 5 because:
    - Directories are no longer stored in the database
      (except for static trees, which are stored as special nodes in the graph.)
    - Deferred globs have been retired and replaced by static trees.
    - the Blake2B hash has been replaced by the more common SHA-256
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
      needed for the new `hold()` context manager (see above).
    - SQLite's `ON DELETE CASCADE` feature is now used for all satellite tables of the `step` table.
    - SQLite triggers are used to replace some of the Python logic by lower-level database logic.
    - All hashes are stored as human-readable JSON blobs.
    - `nglob_multi` data is now stored as JSON instead of a pickle blob,
      for consistency and readability.
    - Removed the now-unused UInt64 adapter/converter,
      no longer needed because of the previous points.
    - Indexes were tuned.
    - The auto_vacuum mode was set to INCREMENTAL,
      which is paired with a database vacuum worker to reclaim space from deleted nodes.
- `amend()` now silently ignores information that the step's plan already declared for it,
  just like it ignores information from an earlier `amend()` call of the same step.
  This lets a plan declare up front what a step also discovers while it runs,
  which improves scheduling (the step is not dispatched before its inputs are available)
  without the step having to know what was declared for it.
  Each argument is matched against its own kind only:
  amending an `out` path that was declared as `vol`, or vice versa, is still an error.
- Several environment variables have been renamed for consistency.
  See [Configuration files](reference/configuration.md) for details.
- A new returncode was added to indicate that the scheduler was put on hold
  and not reporting pending steps.
  See [StepUp Return Codes](reference/returncode.md) for details.
- Documentation has been updated to reflect the API changes and to clarify some other points:
    - All tutorials have been updated to reflect the new API and workflow.
    - A [migration guide](migration/from_3x_to_40.md) has been added
      to help users migrate from StepUp 3 to StepUp 4.
- The `stepup browse` command has become easier to use:
    - When a graphical browser is the default, it opens a browser tab automatically.
    - When a terminal browser is the default, it runs it cleanly in the terminal and exits
      when the terminal browser is closed by the user.
- Updates of many internals, including:
    - Renamed "orphan" and related names to "detached", which is more intuitive.
      The new terminology is applied more consistently with consistent distinction between
      "detach" (verb, state change) and "detached" (state).
    - The "action" abstraction layer introduced in StepUp 3 has been completely removed,
      as it was no longer needed after the introduction of the forkserver.
    - Worker subprocesses have been replaced by asyncio tasks launching subprocesses,
      optionally through a forkserver for reduced overhead.
      File hashing is offloaded to dedicated threads.
    - Strict database sessions management and transaction correctness has been implemented
      to avoid database corruption, e.g. due to race conditions.
    - When a step is detached while it is running (and not recreated before it ends),
      it is still skippable upon a rerun of the build if it gets recreated again.
    - The `STEPUP_STEP_I` environment variable has been replaced by `STEPUP_JOB_I`.
      Instead of a step's (stable) node index, it now holds a unique id for the current
      job running the step, assigned by the scheduler when the job is created, so a
      deferred step's earlier attempt cannot be confused with its later one.
    - Order of `StepInfo` attributes is made consistent with the `step()` API function.
    - The *run phase* has been renamed to *build phase* throughout the documentation and source code.
    - Runner has been renamed to Builder.
    - Cascade has been renamed to Trellis.
    - Supplier has been renamed to Source.
    - Consumer has been renamed to Sink.
- The `Ran N job(s).` message at the end of a build phase now counts only the jobs
  that executed a step's command.
  Skipped steps and internal validation jobs are no longer included,
  which used to make the number confusingly large for builds with many skipped steps.

### Deprecated

- The `stepup boot` command has been deprecated in favor of `sb` or alternatively `stepup build`.
- The script interface for calling user Python scripts from `plan.py` has been deprecated
  in favor of the new [Call](getting_started/call.md) interface.
  You are encouraged to migrate your `plan.py` files to the new API.

### Removed

- StepUp no longer tracks directories.
  They are either assumed to be present (for static files)
  or created transparently right before a step needs it as a workdir or writes an output into them.
  This has some consequences:
    - The `mkdir()` command has been removed.
    - Input and output files can no longer be directories.
  Some of the internal logic that relied on directories being tracked,
  has been refactored to work without them:
    - The watcher uses some simple heuristics to determine which directories to watch.
      It also handles renaming and moving of directories.
    - The cleanup script (`stepup clean`) and the automatic cleanup at the end of a successful run
      will remove empty directories after having removed outdated output files they contained.
    - StepUp now limits its insistence on path affixes (like trailing slashes)
      to only those cases where it is absolutely necessary to avoid ambiguity.
- `--show-perf` has been removed.
  Per-step usage information is stored in the workflow database instead
  and can be viewed with `stepup browse`.
- The `${inp}` and `${out}` placeholders have been removed from the `run()` and `step()` functions.
  Use the `shq()` helper function instead, together with Python's built-in f-strings.
- The `glob()` function no longer accepts `_defer` and `_required` keyword arguments.
- Removed the environment variable substitution in the executable passed to `script()` and `call()`.
- Cross-pattern named-glob consistency (matching several patterns jointly, e.g.
  `glob("feedback_${*idx}.md", "report_${*idx}.pdf")`) is no longer supported.
  It was rarely, if ever, used in practice, and its removal significantly simplifies
  `stepup.core.nglob` and every module that consumes it.
  `NGlobMulti` is removed; `NamedGlob` (unchanged for single-pattern use, and now with
  the convenience methods `NGlobMulti` used to provide) is the only named-glob class.
  It was previously named `NGlobSingle`, a name that only made sense next to a "multi"
  counterpart; `NGlobMatch` is likewise renamed to `NamedGlobMatch`.
  Consistency *within* one pattern (the same `${*name}` reused twice in a single pattern
  string) is unaffected.

### Fixed

- Previously computed file hashes of static files are now reused instead of recomputing them.
- Simple user mistakes no longer dump a full Python traceback:
    - A `ToolError` raised before the director starts
      (e.g. an invalid `stepup build` target) now prints a short `ERROR: ...` message.
    - A build target that resolves to a static file or a volatile output
      on a resumed database (invalid on a fresh database too, but previously only
      contained there) now reports a clean `ERROR` and a `FAILED` exit code,
      instead of crashing the director.
- The progress bar now excludes optional (not rewquired) steps
  correctly from the total count of steps to be executed.
- `Ctrl-C` and `SIGTERM` now abort the build in an orderly fashion.
  The director interrupts all running steps with `SIGINT`,
  kills whatever is still running after a few seconds with `SIGKILL`,
  and only then exits, after writing its logs and final report.
  Previously, the terminal user interface exited immediately,
  which cut the director's shutdown short.
- Sending `SIGTERM` to StepUp no longer leaves running steps behind as orphaned processes.
- The third `q` key press kills running steps with `SIGKILL` again, as documented.
  It escalated to `SIGTERM` instead since version 3.0.0.
- The terminal user interface cleanly exits when the director process fails to start unexpectedly.
- Starting a build no longer refuses to run just because a previous director's socket file
  is still on disk after the process that created it was killed.
  The check now asks the operating system whether the pid advertised in `.stepup/director.log`
  is still alive, and only refuses when it is (or when the pid cannot be determined).
- A keystroke whose command fails inside the director (e.g. `g` when `graph.txt` cannot be
  written) is now reported as an error, and the build carries on.
  Previously this ended `stepup build` with a traceback and discarded the director's return code.
- Running with `--log-level=ERROR` or `--log-level=CRITICAL` no longer ends every successful
  build with a spurious `Errors logged in .stepup/director.log` warning.
- A named wildcard (`${*name}`) now matches the same paths as the anonymous `*` it replaces.
  Previously, `glob("data/${*name}")` silently skipped directory matches,
  while `glob("data/*")` included them.
  Consequently, a named wildcard directly following a separator
  no longer matches an empty string, just like `*` in that position.
  The trailing separator of a matched directory is not part of the captured substring.
- Attempts to use files under `.stepup/` in a workflow will now raise an exception.
- Pressing `Ctrl-Z` now suspends the whole build.
  Steps run in a session of their own, so the terminal never reached them and they kept
  running (and writing files) while StepUp itself was stopped.
  The director now stops them with `SIGSTOP` and continues them on resume,
  and the time spent suspended is no longer recorded as time a step spent working.
- Resuming StepUp with `fg` no longer leaves a broken terminal:
  the cursor stays visible while the build is suspended,
  and keyboard interaction keeps working after the build is resumed.
  Previously every keystroke was echoed and then swallowed by the terminal.
- When an optional step is reverted to pending because nothing needs its output anymore,
  its volatile outputs keep the `VOLATILE` file state.
- A step whose input is changed or deleted while the step is temporarily detached
  from the workflow now runs again once it is recycled.
  Previously it was recycled in its succeeded state and silently kept its stale output.
  This could be observed after an incomplete build (or one run with `--no-clean`),
  which leaves detached steps in the graph for the next build to pick up.

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
[4.0.0rc10]: https://github.com/reproducible-reporting/stepup-core/releases/tag/v4.0.0rc10
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
