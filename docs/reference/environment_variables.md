# Environment Variables

The following environment variables can be used to configure StepUp.

- `STEPUP_DEBUG`: Set to `"1"` to enable debug output.
  This implies `STEPUP_LOG_LEVEL=DEBUG` (if the variable is unset)
  and will require internal consistency checks to pass,
  rather than applying corrections to overcome the inconsistencies.
  (Every such inconsistency is due to a bug, which should be fixed eventually.)
- `STEPUP_EXTERNAL_SOURCES`: a colon-separated list of directories outside `STEPUP_ROOT`
  where automatically detected dependencies should be retained and converted to relative paths.
  This is useful when you have source files that are not part of the StepUp project.
  For example, if you are developing a Python library and use it in a StepUp project,
  changes to the development version of the library will cause StepUp to re-run the affected steps.
  This variable can contain absolute paths and paths relative to `STEPUP_ROOT`.
- `STEPUP_LOG_LEVEL`: The log level to use for the log files in `~/.stepup/`.
  Possible values are `DEBUG`, `INFO`, `WARNING`, `ERROR`, and `CRITICAL`.
  The default is `WARNING`.
  (This can be overridden by the `--log-level` command-line option.)
- `STEPUP_ROOT`: The root directory containing the top-level `plan.py` file.
  If not set, StepUp will look for this file in the current directory.
- `STEPUP_SYNC_RPC_TIMOUT`: The timeout in seconds for the synchronous RPC server.
  The default is 300 seconds.
  Set this to a smaller value if you want to detect deadlocks more quickly.

We recommend using tools like [direnv](https://direnv.net/) to manage these variables.
Once `direnv` is installed and configured, you can create a `.envrc` file
in the root directory of your project, e.g. with the following contents:

```bash
export STEPUP_ROOT=${PWD}
```

When you change to the project directory (or any of its subdirectories) in your shell,
the environment variables will be set automatically.
