<!--
SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
SPDX-License-Identifier: CC-BY-SA-4.0
-->
# StepUp Configuration

StepUp can be configured using configuration files, environment variables, and command-line options.
When a `stepup` tool starts, e.g. `sb`,
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

Settings shared by all subcommands are placed at the top level of the config file
(or under `[tool.stepup]` in `pyproject.toml`).
Settings specific to a subcommand, e.g. `build`, go under a subcommand-specific section,
e.g. `[build]` (or `[tool.stepup.build]` in `pyproject.toml`).

Example `stepup.toml`:

```toml
log_level = "INFO"

[build]
jobs = 4
watch = true
```

Having multiple configuration files is convenient but can be confusing.
StepUp provides a `stepup config` tool to help you understand which settings are in effect.
This tool reads all the configuration files and environment variables,
and shows the merged settings as a single, informative TOML file,
including comments about the source of each setting.

Positional command-line arguments (targets to build or paths to clean)
cannot be set through configuration files or environment variables.

## Configuration Errors

A subcommand refuses to start when anything is wrong with a configuration file:
invalid TOML syntax, an unknown section, a key that is not a setting
of the section it appears in, or a value that a setting cannot use.
The value of an environment variable is checked in the same way,
but its **name** is not: see [Unrecognized Environment Variables](#unrecognized-environment-variables).
All problems are reported at once, so that a single run tells you everything to fix.
Each one names the config file the way `stepup config` does,
relative to the working directory when it lies below it:

```text
$ sb
ERROR: Problems with the StepUp configuration:
  ./stepup.toml: unsupported key 'speed' in section [build]
  ./stepup.toml: unknown section [buidl] (did you mean 'build'?)
Run 'stepup config' to inspect the configuration.
```

This sets the first bit of the [return code](returncode.md),
without a Python traceback unless `STEPUP_DEBUG` is set.
Problems are shown in red when the terminal supports color.

The `stepup config` subcommand is the one exception:
it still prints the configuration it loaded, with the source of each setting.
This makes it usable precisely when the configuration is broken.
Each problem is shown on the line of the setting, section or config file it concerns,
for the same `stepup.toml` as above:

```toml
# Config files (lowest to highest priority):
#   MISSING: /etc/stepup.toml
#   MISSING: ~/.config/stepup.toml
#   MISSING: .stepup.toml
#   FOUND:   ./stepup.toml
#   MISSING: ./pyproject.toml
#   MISSING: ./stepup-local.toml
# Environment variables: STEPUP_*

[buidl]  # <-- ERROR: ./stepup.toml: unknown section [buidl] (did you mean 'build'?)
jobs = 2  # ./stepup.toml

[build]
speed = 4  # ./stepup.toml  <-- ERROR: unsupported key 'speed' in section [build]
```

The problems are written in comments, so the output remains valid TOML.
A problem that has no line of its own is listed afterwards on standard error.
This happens when a config file with a higher priority overrides the setting at fault,
and when a line already carries another problem,
because a second comment cannot be opened on the same line.

## Unrecognized Environment Variables

A misspelled key in a config file is an error, but a misspelled `STEPUP_*` variable is not.
The `STEPUP_*` namespace is shared with the variables that configure StepUp's internals,
listed under [StepUp Core Module Environment Variables](#stepup-core-module-environment-variables),
which no subcommand defines an option for.
Rejecting every name that is not a setting would therefore reject StepUp's own environment.

To make a typo visible anyway, `stepup config` lists the `STEPUP_*` variables
in three groups, by what each of them does:

```toml
# Configuration environment variables:
#   STEPUP_BUILD_JOBS = "4"

# StepUp Core module environment variables:
#   STEPUP_ROOT = "/home/user/project"

# Unrecognized environment variables, without effect:
#   STEPUP_BUILD_JBOS = "4"
```

When a setting does not take effect, the last group is the first place to look.
It holds every `STEPUP_*` variable that StepUp does not act on,
which for a variable you set deliberately means a misspelled name.
Variables of an extension package appear there as well
when they configure that package's internals rather than one of its settings.

## StepUp Core Module Environment Variables

Some environment variables affect StepUp's internals even when it is just imported as a Python library.
These can only be set via environment variables,
and cannot be configured through config files or command-line options.

`STEPUP_MAX_OUTPUT_SIZE`

:   The maximum size of standard output and standard error stored in the workflow database.
    The default is `0`, meaning unlimited (no truncation).
    This limit only affects what is persisted, not the terminal output.
    When a stream exceeds the limit, it is truncated on a UTF-8 character
    boundary and a `[output truncated at N bytes]` line is appended.

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

`STEPUP_ROOT`

:   The root directory containing the top-level `plan.py` file.
    If not set, StepUp will look for this file in the current directory.

`STEPUP_SYNC_RPC_TIMEOUT`

:   The timeout in seconds for the synchronous RPC server.
    The default is 300 seconds.
    Set this to a smaller value if you want to detect deadlocks more quickly.

## Settings for All Subcommands

Each entry below lists the config file key, environment variable, and command-line option
separated by slashes, where applicable.

`STEPUP_DEBUG`

:   Set to `1` to enable debugging features and strict consistency checks.
    This implies `STEPUP_LOG_LEVEL=DEBUG` (if the variable is unset)
    and will require internal consistency checks to pass,
    rather than applying corrections to overcome the inconsistencies.
    (Every such inconsistency is due to a bug, which should be fixed eventually.)
    It also makes the scan of `.stepup/director.log` at the end of a build fatal:
    a logged error or a coroutine, task or thread left dangling is reported as an error
    and sets the internal error bit of the [return code](returncode.md),
    instead of only being reported as a warning.
    Finally, it disables the shortening of error reports described in
    [Failing Steps](../getting_started/failing_steps.md):
    a failing step then prints its complete traceback,
    including StepUp's own frames and the traceback of the director process.
    This variable cannot be set through config files or command-line options.

`log_level` / `STEPUP_LOG_LEVEL` / `--log-level`, `-l`

:   The log level for the log files in `~/.stepup/`.
    Possible values are `DEBUG`, `INFO`, `WARNING`, `ERROR`, and `CRITICAL`.
    The default is `WARNING`.

## Settings for `sb` or `stepup build`

These settings are stored under the `[build]` section in config files
(or `[tool.stepup.build]` in `pyproject.toml`).
Each entry below lists the config file key, environment variable, and command-line option
separated by slashes, where applicable.
The settings are grouped as in the output of `stepup build --help`,
alphabetically within each group.

### Build Control

`clean` / `STEPUP_BUILD_CLEAN` / `--clean`, `--no-clean`

:   Set to `false` to disable automatic cleaning of outdated output files.
    By default, StepUp automatically removes old output files that are no longer created
    by any step in the workflow.

`duration` / `STEPUP_BUILD_DURATION` / `--duration`, `--no-duration`

:   Set to `false` to disable recording of step wall times as their `duration` for future runs.
    StepUp uses the duration information to prioritize the execution
    of steps with the longest critical path (in time units) to any terminal node.
    (This is stored in the database as the `_tail_time` field of each step.)
    While accurate durations are generally beneficial,
    they can result in non-deterministic execution order,
    which can be undesirable in some cases, such as testing.

    When disabled, durations remain unchanged from the previous run
    or stay at their initial value if the step has never been executed before.
    The initial value can be provided when defining the step, or defaults to `1.0` if not provided.
    With the `1.0` default, the `_tail_time` of a step degrades to
    the number of steps in the longest path to any terminal node.

`explain_rerun` / `STEPUP_BUILD_EXPLAIN_RERUN` / `--explain-rerun`, `-e`, `--no-explain-rerun`

:   Set to `true` to explain for every step with recording info why it cannot be skipped.

`fix_epoch` / `STEPUP_BUILD_FIX_EPOCH` / `--fix-epoch`, `--no-fix-epoch`

:   If set to `true` (the default), the `SOURCE_DATE_EPOCH` environment variable
    will be set to a fixed value of `315532800`
    (corresponding to 1980-01-01 00:00:00 UTC) for all step executions.
    This is useful for ensuring reproducible builds:
    Many tools and libraries recognize `SOURCE_DATE_EPOCH` and
    use it instead of the current time for timestamps in generated files.
    If the variable is already set in the environment, it will be used as-is.

`jobs` / `STEPUP_BUILD_JOBS` / `--jobs`, `-j`

:   The maximum number of steps to run concurrently.
    When given as a floating point number, the value is multiplied by the number of available CPU cores.
    The default is `1.0`.

`keep_going` / `STEPUP_BUILD_KEEP_GOING` / `--keep-going`, `-k`, `--no-keep-going`

:   Set to `true` to keep dispatching new steps after another step has failed,
    as long as their own inputs remain available (like `make -k`).
    By default (`false`), the scheduler starts draining after the first failure:
    steps already running are still allowed to finish, but no new steps are started.

`defer_cap` / `STEPUP_BUILD_DEFER_CAP` / `--defer-cap`

:   Maximum number of times a step can be deferred (since it last succeeded)
    before it is reported as failed instead of parked pending again.
    This guards against livelocks where a step's dynamic inputs keep flip-flopping.
    The default is `100`, deliberately generous.

`progress` / `STEPUP_BUILD_PROGRESS` / `--progress`, `--no-progress`

:   Set to `false` to disable the progress bar in the terminal user interface.
    This can be useful to simplify and reduce the output.

`resources` / `STEPUP_BUILD_RESOURCES` / `--resources`, `-r`

:   A comma-separated list of resource names and available quantities
    to be used for scheduling decisions.
    For example, `resources = "gpu:2,cpu:4"` indicates that there are 2 GPUs and 4 CPUs available.
    Any resource labels can be used, and the available quantity can be any positive integer.
    Note that resource specifications from config files, the environment variable,
    and one ore more CLI options (from left to right, e.g. `-r gpu:2 -r cpu:4`) are merged together.

`watch` / `STEPUP_BUILD_WATCH` / `--watch`, `-w`, `--no-watch`

:   Set to `true` to enable watch mode.
    In watch mode, StepUp will monitor the file system for changes
    and rerun affected steps after pressing the `r` key in the terminal user interface.
    Only supported on Linux.

`watch_first` / `STEPUP_BUILD_WATCH_FIRST` / `--watch-first`, `-W`, `--no-watch-first`

:   Set to `true` to automatically rerun affected steps
    when relevant file changes are observed,
    without needing to press the `r` key.
    This implies `watch = true`.
    Only supported on Linux.

### Execution Environment

`cgroup` / `STEPUP_BUILD_CGROUP` / `--cgroup`, `--no-cgroup`

:   This setting controls whether StepUp will run the director
    (and all its child processes running steps) use cgroup isolation.
    When enabled, peak memory usage of the director process and all its child processes is measured.
    Only available on Linux with cgroup v2 enabled and if `systemd-run` is available.
    Exceptions are raised if cgroup isolation is requested but not working.
    Off by default.

`forkserver` / `STEPUP_BUILD_FORKSERVER` / `--forkserver`, `--no-forkserver`

:   Set to `true` to use a forkserver for Python step execution,
    which reduces startup overhead.
    This is enabled by default on Linux.

`preload_modules` / `STEPUP_BUILD_PRELOAD_MODULES` / `--preload-modules`

:   A comma-separated list of Python modules to pre-load into the forkserver.
    Only has effect when `forkserver = true`.
    Use this to reduce per-step startup time when all (or most) steps import the same large modules.
    For example, `preload_modules = "numpy,scipy"` pre-loads NumPy and SciPy into the forkserver
    so that each Python step forked from it inherits them at zero import cost.
    By default, no additional modules are pre-loaded (only internal StepUp modules are pre-loaded).

### Diagnostics and Profiling

`joblog` / `STEPUP_BUILD_JOBLOG` / `--joblog`, `--no-joblog`

:   Set to `true` to record job-execution events to `.stepup/joblog.csv`, one row per event,
    with columns `time_ns`, `job_i`, `event`, `description`.
    The file is truncated and rewritten at the start of every build phase.
    Each job produces four events:
    - `CREATED` (by the scheduler),
    - `STARTED` and `ENDED` (by the executor),
    - `COMPLETED` (observed by the scheduler, freeing a slot for the next job).
    Comparing the timestamps across these events, and deriving the number of concurrently
    running jobs from them, helps diagnose scheduler/executor dispatch overhead.

`perf` / `STEPUP_BUILD_PERF` / `--perf`

:   Set to a frequency in Hz to enable performance monitoring of the director process
    with the [Linux perf profiler](https://perfwiki.github.io/main/).
    See the section on [Profiling](../development.md#profiling)
    in the development documentation for more details.

`sqllog` / `STEPUP_BUILD_SQLLOG` / `--sqllog`, `--no-sqllog`

:   Set to `true` to enable SQLite debug logging.
    Each `execute()` / `executemany()` call appends a timing row to `.stepup/sqllog.csv`
    as it happens.
    A `.stepup/sqllog.json` index (query text, call site, query plan, and the `query_i` id
    referenced by the CSV rows) is written when the director exits.

`yappi` / `STEPUP_BUILD_YAPPI` / `--yappi`, `--no-yappi`

:   Set to `true` to profile the director process with the [Yappi profiler](https://github.com/sumerc/yappi).
    See the section on [Profiling](../development.md#profiling)
    in the development documentation for more details.

The targets to build (see [Build Targets](../advanced_topics/build_targets.md))
are positional command-line arguments
and cannot be set through config files or environment variables.
When no targets are given, the full default workflow is built.

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

`STEPUP_JOB_I`

:   A unique integer id for the current job running the step.
    Unlike a step's own (stable) index, this changes every time the step is (re)started,
    e.g. after being deferred.
    This is mainly relevant for StepUp and has little significance for users implementing workflows.

`STEPUP_STEP_INP_DIGEST`

:   A hex-formatted digest of all the inputs to the step
    (including environment variables used).
    This is useful in special cases.
    For example, it can be used to decide if cached results
    from a previously interrupted run of the step are still valid.
    It can also be useful when a step submits a job to an external scheduler,
    to decide if a previously submitted job is still valid.

`STEPUP_STEP_NEED`

:   The declared need level of the currently executing step,
    as one of the strings `OPTIONAL`, `DEFAULT`, `TARGET`, or `PLAN`.
    This is used internally by StepUp to detect workflow authoring errors,
    such as registering a planning step (`need=Need.PLAN`) from inside a non-planning step.
