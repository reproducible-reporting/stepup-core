# Environment Variables

## Configuration of StepUp

The following environment variables can be used to configure StepUp.

`STEPUP_CLEAN`

: Set to `0` to disable automatic cleaning of outdated output files.
By default, StepUp automatically removes old output files that are no longer created
by any step in the workflow.
Overridden by the `--clean` and `--no-clean` command-line options.

`STEPUP_DEBUG`

: Set to `1` to enable debug output.
This implies `STEPUP_LOG_LEVEL=DEBUG` (if the variable is unset)
and will require internal consistency checks to pass,
rather than applying corrections to overcome the inconsistencies.
(Every such inconsistency is due to a bug, which should be fixed eventually.)

`STEPUP_EXPLAIN_RERUN`

: Set to `1` to explain for every step with recording info
why it cannot be skipped.
Overridden by the `--explain-rerun` and `--no-explain-rerun` command-line options.

`STEPUP_LOG_LEVEL`

: The log level to use for the log files in `~/.stepup/`.
Possible values are `DEBUG`, `INFO`, `WARNING`, `ERROR`, and `CRITICAL`.
The default is `WARNING`.
Overridden by the `--log-level` command-line option.

`STEPUP_PATH_FILTER`

: a colon-separated list of filters
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

`STEPUP_PERF`

: Set to a frequency in Hz to enable performance monitoring of the director process
with Linux perf profiler.
Overridden by the `--perf` command-line option.

`STEPUP_PROGRESS`

: Set to `0` to disable the progress bar in the terminal user interface.
This can be useful to simplify and reduce the output.
Overridden by the `--progress` and `--no-progress` command-line options.

`STEPUP_ROOT`

: The root directory containing the top-level `plan.py` file.
If not set, StepUp will look for this file in the current directory.

`STEPUP_SHOW_PERF`

: Set to `1` to show basic performance information
(like execution time) for each step in the terminal user interface.
Set to `2` to show more detailed performance information.
Overridden by the `--show-perf` command-line option.

`STEPUP_SYNC_RPC_TIMOUT`

: The timeout in seconds for the synchronous RPC server.
The default is 300 seconds.
Set this to a smaller value if you want to detect deadlocks more quickly.

`STEPUP_WATCH`

: Set to `1` to enable watch mode by default.
In watch mode, StepUp will monitor the file system for changes
and rerun affected steps after pressing the `r` key in the terminal user interface.
Overridden by the `--watch` and `--no-watch` command-line options.

`STEPUP_WATCH_FIRST`

: Set to `1` to automatically rerun affected steps
when relevant file changes are observed.
Hence, there is no need to press the `r` key, and can leave the terminal UI unattended.
This implies `STEPUP_WATCH=1`.
Overridden by the `--watch-first` and `--no-watch-first` command-line options.

We recommend using tools like [direnv](https://direnv.net/) to manage these variables.
Once `direnv` is installed and configured, you can create a `.envrc` file
in the root directory of your project, e.g. with the following contents:

```bash
export STEPUP_ROOT=${PWD}
```

When you change to the project directory (or any of its subdirectories) in your shell,
the environment variables will be set automatically.

## Environment Variables in Worker Processes

The following environment variables are set when a worker runs a step:

`HERE` and `ROOT`

: These are documented in the tutorial on
[`HERE` and `ROOT` variables](../advanced_topics/here_and_root.md)

`STEPUP_STEP_I`

: A unique integer index for the current step.
This is mainly relevant for StepUp and has little significance for users implementing workflows.

`STEPUP_STEP_INP_DIGEST`

: A hex-formatted digest of all the inputs to the step
(including environment variables used).
This is useful in special cases.
For example, it can be used to decide if cached results from a previously interrupted run of the step
are still valid.
It can also be useful when actions submit jobs to a scheduler,
to decide if a running job is still valid.
