# The STEPUP_ROOT variable

If you are working on a large project with several subdirectories,
it may be useful to define the `STEPUP_ROOT` environment variable.
It should contain the absolute path of the top-level directory where you would normally call the `stepup` and `cleanup` commands.
(The top-level directory contains the `.stepup` subdirectory and the top-level `plan.py`.)

With `STEPUP_ROOT` set, it is no longer necessary to change to the top-level directory before running `stepup` and `cleanup`.
Also, the `cleanup` arguments will be interpreted correctly in subdirectories.

You can manually set `STEPUP_ROOT` in the top-level directory as follows:

```bash
export STEPUP_ROOT="${PWD}"
```

However, this can be tedious, as it has to be set each time you open a new terminal window.
It is much easier to set such variables using [direnv](https://direnv.net/).
Once direnv is configured on your system, you can create an `.envrc` file with the above `export` line in the top-level directory.
Each time you change to the project directory or any of its subdirectories, the `STEPUP_ROOT` directory will automatically be set correctly.
