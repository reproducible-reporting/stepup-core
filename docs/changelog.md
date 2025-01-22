# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).


## [Unreleased]

(no changes yet)

## [2.0.1] - 2025-01-22 {: #v2.0.1 }

(Version 2.0.0 was yanked due to a packaging issue)

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
- [`getinfo()`][stepup.core.api.getinfo] function to retrieve the
  [StepInfo][stepup.core.stepinfo.StepInfo] object of the current step.
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
      and [`mkdir()`][stepup.core.api.mkdir] must be given as keyword arguments.
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
    - The "supplier ➜ consumer" graph is now called the dependency graph.
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

- When static file has been deleted (missing) and later restored, the restored file was not noticed when restarting StepUp. This is fixed.
- Tests have been made compatible with Python 3.13.
- Files with whitespace are handled correctly.
  (That being said, we don't recommend using files with whitespace.)


## [1.3.1] - 2024-09-17 {: #v1.3.1 }

### Fixed

- Fix incorrect parsing of `?*` and `*?` wildcards in the `nglob` module.

## [1.3.0] - 2024-08-27 {: #v1.3.0 }

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


## [1.2.8] - 2024-06-28 {: #v1.2.8 }

### Fixed

- Modify the script driver so that `info()` and `case_info()` may return empty dictionaries.


## [1.2.7] - 2024-06-24 {: #v1.2.7 }

### Fixed

- Add workaround for Python==3.11 bug with RPC over sockets.
  The RPC server (created with `asyncio.start_unix_server`) closes before all requests are handled.
  A stop event is now included for all RPC handlers
  to wait with stopping the server until every request is handled.
  This is a known issue fixed in Python 3.12.1:
  https://github.com/python/cpython/issues/120866


## [1.2.6] - 2024-06-13 {: #v1.2.6 }

### Fixed

- Do not watch files when running StepUp non-interactively.
  This makes non-interactive mode a workaround for a nasty watchdog bug,
  which crops up when working on larger StepUp projects.
  See https://github.com/gorakhargosh/watchdog/issues/275


## [1.2.5] - 2024-06-13 {: #v1.2.5 }

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


## [1.2.4] - 2024-05-27 {: #v1.2.4 }

### Changed

- Include "hidden" files when globbing.

### Fixed

- Do not refuse to replay unchanged step that declares its own static inputs.
- Make recursive glob consistent with Python's built-in glob in `step.core.nglob`.
- Pool definitions are stored in workflow and replayed correctly when a step is skipped.


## [1.2.3] - 2024-05-19 {: #v1.2.3 }

### Changed

- Completed and revised docstrings in `stepup.core.nglob`,
  and added this module to the reference documentation.

### Fixed

- Improve hash computation of a symbolic links in `stepup.core.hash`.


## [1.2.2] - 2024-05-16 {: #v1.2.2 }

### Changed

- Documentation updates.

### Fixed

- Make `cleanup` command work in project subdirectories when `STEPUP_ROOT` is set.
- Avoid useless wait when running a `plan.py` script outside of `stepup`.


## [1.2.1] - 2024-05-07 {: #v1.2.1 }

### Fixed

- Fixed packaging mistake that confused PyCharm and Pytest.


## [1.2.0] - 2024-05-02 {: #v1.2.0 }

### Added

- Export of graphs to [Graphviz](https://graphviz.org/) DOT files.
- The `cleanup` script for manually cleaning up outputs.

### Changed

- Documentation updates.
- Limit acyclic constraint to the supplier-consumer graph.
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


## [1.0.0] - 2024-04-25 {: #v1.0.0 }

Initial release


[Unreleased]: https://github.com/reproducible-reporting/stepup-core
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
