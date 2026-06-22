# StepUp Configuration

StepUp can be configured using configuration files, environment variables, and command-line options.
When a `stepup` tool starts, e.g. `stepup boot`,
it will load its settings in the following order, with later settings overriding earlier ones:

- `/etc/stepup.toml` (system-wide configuration file)
- `~/.config/stepup.toml` (user-wide configuration file)
- `${STEPUP_ROOT}/.stepup.toml` (project-specific configuration file)
- `${STEPUP_ROOT}/stepup.toml` (project-specific configuration file)
- `${STEPUP_ROOT}/pyproject.toml`
  (project-specific configuration file,
  with settings under the `[tool.stepup]` section)
- `${STEPUP_ROOT}/stepup-local.toml`
  (project-specific configuration file,
  intended for local overrides that are not committed to version control)
- Environment variables (`STEPUP_*`)
- Command-line options

Settings for all subcommands are placed at the top level of the config file
(or under `[tool.stepup]` in `pyproject.toml`).
Settings specific to the `boot` subcommand go under a `[boot]` section
(or `[tool.stepup.boot]` in `pyproject.toml`).

Example `stepup.toml`:

```toml
log_level = "INFO"

[boot]
jobs = 4
watch = true
```

Having multiple configuration files is convenient but can be confusing.
StepUp provides a `stepup show-config` tool to help you understand which settings are in effect.
This tool reads all the configuration files and environment variables,
and shows the merged settings as a single, informative TOML file,
including comments about the source of each setting.

## Internal environment variables

Some environment variables affect StepUp's internals even when it is just imported as a Python library.
These can only be set via environment variables,
and cannot be configured through config files or command-line options.

`STEPUP_SYNC_RPC_TIMEOUT`

:   The timeout in seconds for the synchronous RPC server.
    The default is 300 seconds.
    Set this to a smaller value if you want to detect deadlocks more quickly.

`STEPUP_PATH_FILTER`

:   A colon-separated list of filters
    for determining whether to retain or ignore an automatically detected dependency.
    Each item starts with a `+` or `-` sign, followed by a path prefix to be used for matching.
    The items in the filter are processed in order, and the first match determines the action.
    If the matching path prefix is preceded by a `-`, the dependency is ignored.
    If it is preceded by a `+`, the dependency is retained and rewritten relative to `${STEPUP_ROOT}`.
    A path in the filter can be absolute or relative to `${STEPUP_ROOT}`,
    but matching is always done based on absolute paths.
    The default is `-venv`.
    Regardless of whether a filter is defined, the filters `:+.:-/` are always appended.
    This feature can be used for several purposes:

    - You may have source files that are not part of the StepUp project,
      but are used in the project and edited frequently.
      In this case, steps that depend on these external files
      will be rerun when you change the external source files.

    - You have a virtual environment with many packages installed,
      but you don't want to include them in the dependency graph for performance reasons.
      (This is done by default for the `venv` virtual environment.)

## Settings for all subcommands

Each entry below lists the config file key, environment variable, and command-line option
separated by slashes, where applicable.

`STEPUP_DEBUG`

:   Set to `1` to enable debug output.
    This implies `STEPUP_LOG_LEVEL=DEBUG` (if the variable is unset)
    and will require internal consistency checks to pass,
    rather than applying corrections to overcome the inconsistencies.
    (Every such inconsistency is due to a bug, which should be fixed eventually.)
    This variable cannot be set through config files or command-line options.

`log_level` / `STEPUP_LOG_LEVEL` / `--log-level`, `-l`

:   The log level for the log files in `~/.stepup/`.
    Possible values are `DEBUG`, `INFO`, `WARNING`, `ERROR`, and `CRITICAL`.
    The default is `WARNING`.

`STEPUP_ROOT` / `--root`

:   The root directory containing the top-level `plan.py` file.
    If not set, StepUp will look for this file in the current directory.
    This setting cannot be configured through config files.

## Settings for `stepup boot`

These settings are stored under the `[boot]` section in config files
(or `[tool.stepup.boot]` in `pyproject.toml`).
Each entry below lists the config file key, environment variable, and command-line option
separated by slashes, where applicable.

`clean` / `STEPUP_BOOT_CLEAN` / `--clean`, `--no-clean`

:   Set to `false` to disable automatic cleaning of outdated output files.
    By default, StepUp automatically removes old output files that are no longer created
    by any step in the workflow.

`duration` / `STEPUP_BOOT_DURATION` / `--duration`, `--no-duration`

:   Set to `false` to disable the use of step duration information for scheduling decisions.
    By default, StepUp uses the duration information of steps to prioritize the execution
    of steps that are expected to take longer, which can lead to faster overall execution.
    While this is generally beneficial, it can result in non-deterministic execution order,
    which can be undesirable in some cases, such as testing.

`explain_rerun` / `STEPUP_BOOT_EXPLAIN_RERUN` / `--explain-rerun`, `-e`, `--no-explain-rerun`

:   Set to `true` to explain for every step with recording info why it cannot be skipped.

`fork_runpy` / `STEPUP_BOOT_FORK_RUNPY` / `--fork-runpy`, `--no-fork-runpy`

:   Set to `true` to use a forkserver for Python step execution and file hashing,
    which reduces startup overhead.
    This is enabled by default on Linux.

`preload_modules` / `STEPUP_BOOT_PRELOAD_MODULES` / `--preload-modules`

:   A comma-separated list of Python modules to pre-load into the forkserver.
    Only has effect when `fork_runpy = true`.
    Use this to reduce per-step startup time when all (or most) steps import the same large modules.
    For example, `preload_modules = "numpy,scipy"` pre-loads NumPy and SciPy into the forkserver
    so that each Python step forked from it inherits them at zero import cost.
    By default, no additional modules are pre-loaded (only internal StepUp modules are pre-loaded).

`jobs` / `STEPUP_BOOT_JOBS` / `--jobs`, `-j`

:   The maximum number of steps to run concurrently.
    When given as a floating point number, the value is multiplied by the number of available CPU cores.
    The default is `1.2`.

`perf` / `STEPUP_BOOT_PERF` / `--perf`

:   Set to a frequency in Hz to enable performance monitoring of the director process
    with the [Linux perf profiler](https://perfwiki.github.io/main/).
    See the section on [Profiling](../development.md#profiling)
    in the development documentation for more details.

`progress` / `STEPUP_BOOT_PROGRESS` / `--progress`, `--no-progress`

:   Set to `false` to disable the progress bar in the terminal user interface.
    This can be useful to simplify and reduce the output.

`resources` / `STEPUP_BOOT_RESOURCES` / `--resources`, `-r`

:   A comma-separated list of resource names and available quantities
    to be used for scheduling decisions.
    For example, `resources = "gpu:2,cpu:4"` indicates that there are 2 GPUs and 4 CPUs available.
    Any resource labels can be used, and the available quantity can be any positive integer.
    Note that resource specifications from config files, the environment variable, and the CLI option
    are merged together, with the CLI option taking precedence over the environment variable.

`show_perf` / `STEPUP_BOOT_SHOW_PERF` / `--show-perf`, `-s`

:   Set to `1` to show basic performance information
    (like execution time) for each step in the terminal user interface.
    Set to `2` to show more detailed performance information.

`watch` / `STEPUP_BOOT_WATCH` / `--watch`, `-w`, `--no-watch`

:   Set to `true` to enable watch mode.
    In watch mode, StepUp will monitor the file system for changes
    and rerun affected steps after pressing the `r` key in the terminal user interface.
    Only supported on Linux.

`watch_first` / `STEPUP_BOOT_WATCH_FIRST` / `--watch-first`, `-W`, `--no-watch-first`

:   Set to `true` to automatically rerun affected steps
    when relevant file changes are observed,
    without needing to press the `r` key.
    This implies `watch = true`.
    Only supported on Linux.

`yappi` / `STEPUP_BOOT_YAPPI` / `--yappi`, `--no-yappi`

:   Set to `true` to profile the director process with the [Yappi profiler](https://github.com/sumerc/yappi).
    See the section on [Profiling](../development.md#profiling)
    in the development documentation for more details.

## Settings for `stepup clean`

These settings are stored under the `[clean]` section in config files
(or `[tool.stepup.clean]` in `pyproject.toml`).
Each entry below lists the config file key, environment variable, and command-line option
separated by slashes, where applicable.

`all` / `STEPUP_CLEAN_ALL` / `--all`, `-a`

:   Set to `true` to remove outputs of *any* step in the workflow,
    not just detached outputs (those for which no corresponding step exists anymore).
    Whenever a file is removed, outputs depending on it are also removed.
    The default is `false`.

`commit` / `STEPUP_CLEAN_COMMIT` / `--commit`, `-c`

:   Set to `true` to actually remove files and directories instead of performing a dry run.
    By default, `stepup clean` only prints what would be removed without deleting anything.

`safe` / `STEPUP_CLEAN_SAFE` / `--unsafe`, `-u`

:   Set to `false` to also remove output files that have been modified after their creation
    in the workflow.
    By default (`true`), modified files are skipped and reported as a warning.
    Note that the CLI flag is `--unsafe`, which is the negation of the config key `safe`.

The paths to consider for cleanup are positional command-line arguments
and cannot be set through config files or environment variables.
When no paths are given, the current directory is used.

## Settings for `stepup browse`

These settings are stored under the `[browse]` section in config files
(or `[tool.stepup.browse]` in `pyproject.toml`).
Each entry below lists the config file key, environment variable, and command-line option
separated by slashes, where applicable.

`port` / `STEPUP_BROWSE_PORT` / `--port`

:   The port number for the local web server that serves the build graph browser.
    The default is `8000`.
    After starting, the server is accessible at `http://localhost:<port>/`.

## Environment Variables in Step Execution

The following environment variables are set when a step executes.
These are mainly relevant for StepUp's internals,
but can be useful for users implementing workflows.
Note that anything in the step execution (sub)processes is also affected by
the internal environment variables described above.

`HERE` and `ROOT`

:   These are documented in the tutorial on
    [`HERE` and `ROOT` variables](../advanced_topics/here_and_root.md)

`STEPUP_STEP_I`

:   A unique integer index for the current step.
    This is mainly relevant for StepUp and has little significance for users implementing workflows.

`STEPUP_STEP_INP_DIGEST`

:   A hex-formatted digest of all the inputs to the step
    (including environment variables used).
    This is useful in special cases.
    For example, it can be used to decide if cached results
    from a previously interrupted run of the step are still valid.
    It can also be useful when actions submit jobs to a scheduler,
    to decide if a running job is still valid.

`STEPUP_STEP_NEED`

:   The declared need level of the currently executing step,
    as one of the strings `OPTIONAL`, `DEFAULT`, `TARGET`, or `PLAN`.
    This is used internally by StepUp to detect workflow authoring errors,
    such as registering a planning step (`need=Need.PLAN`) from inside a non-planning step.
