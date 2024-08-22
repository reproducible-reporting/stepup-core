# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Add support for standard output and error redirection in the script driver.
  The dictionary returned by the `info` or `case_info` functions
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
  i.e. when the `files` method or property has exactly one path.


### Changed

- Migrate `load_module_file` to stepup-reprep.
- Replace [watchdog](https://github.com/gorakhargosh/watchdog)
  by [asyncinotify](https://github.com/ProCern/asyncinotify)
  to avoid [a long-standing issue in watchdog](https://github.com/gorakhargosh/watchdog/issues/275).


### Fixed

- Fix bug in the translation of relative paths before they are sent to the director process.
- Add trailing slash to `workdir` argument of `stepup.core.api.step()` if it is missing.
- Fix mistake in worker log filenames.
- Fix bug in back translation of paths when substituted in a step command.
- Improve compatibility of nglob with Python's built-in glob.


## [1.2.8] - 2024-06-28 {: #v1.2.8 }

### Fixed

- Modify the script driver so that `info` and `case_info` may return empty dictionaries.


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
        - `watch_add` -> `watch_update`
        - `watch_del` -> `watch_delete`
    - Separate watcher and runner coroutines with reduced risk for race conditions related to
      `watch_delete` and `watch_update` to address `TimeoutError`.
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
