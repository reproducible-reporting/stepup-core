# Environment Variables

The following environment variables can be used to configure StepUp.

- `STEPUP_ROOT`: The root directory containing the top-level `plan.py` file.
  If not set, StepUp will look for this file in the current directory.
- `STEPUP_STRICT`: Set to `"1"` to enable strict checks.
  This will make StepUp fail on some internal inconsistency checks,
  rather than applying corrections to overcome the inconsistencies.
  (Every such inconsistency is due to a bug, which should be fixed eventually.)

We recommend using tools like [direnv](https://direnv.net/) to manage these variables.
Once `direnv` is installed and configured, you can create a `.envrc` file
in the root directory of your project, e.g. with the following contents:

```bash
export STEPUP_ROOT=${PWD}
```

As you change to the project directory (or any of its subdirectories) in your shell,
the environment variables will be set automatically.
