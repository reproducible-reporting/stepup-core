# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.2.3] - 2024-05-19

### Changed

- Completed and revised docstrings in `stepup.core.nglob`,
  and added this module to the reference documentation.

### Fixed

- Improve hash computation of a symbolic links in `stepup.core.hash`.


## [1.2.2] - 2024-05-16

### Fixed

- Make `cleanup` command work in project subdirectories when `STEPUP_ROOT` is set.
- Avoid useless wait when running a `plan.py` script outside of `stepup`.

### Changed

- Documentation updates.


## [1.2.1] - 2024-05-07

### Fixed

- Fixed packaging mistake that confused PyCharm and Pytest.


## [1.2.0] - 2024-05-02

### Added

- Export of graphs to [Graphviz](https://graphviz.org/) DOT files.
- The `cleanup` script for manually cleaning up outputs.

### Fixed

- More graceful error message when the director process crashes early.
- Fix compatibility with [asciinema](https://asciinema.org) terminal recording.
- Raise `ConnectionResetError` in `SocketSyncRPCClient` instead of blocking forever when
  the director process crashes.

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


## [1.0.0] - 2024-04-25

Initial release
