# The STEPUP_ROOT variable

If you are working on a large project with several subdirectories,
it may be useful to define the `STEPUP_ROOT` environment variable.
It should contain the absolute path of the top-level directory
where you would normally call the `stepup` command.
(The top-level directory contains the `.stepup` subdirectory and the top-level `plan.py`.)

With `STEPUP_ROOT` set, it is no longer necessary to change to the top-level directory
before running `sb`.
Also, `stepup clean` arguments will be interpreted correctly in subdirectories.

Despite the similar name, `STEPUP_ROOT` is unrelated to the `${ROOT}` substitution variable.
`STEPUP_ROOT` is an **absolute** path that you set to tell the `stepup` command where the
project lives, whereas `${ROOT}` is a **relative** path that StepUp defines for a running step.
See [HERE and ROOT Variables](here_and_root.md) for the latter.

You can manually set `STEPUP_ROOT` in the top-level directory as follows:

```bash
export STEPUP_ROOT="${PWD}"
```

However, this can be tedious, as it has to be set each time you open a new terminal window.
It is much easier to set such variables using [direnv](https://direnv.net/),
a tool that automatically loads and unloads environment variables as you enter and leave a directory.
Once direnv is configured on your system,
you can create an `.envrc` file with the above `export` line in the top-level directory.
Each time you change to the project directory or any of its subdirectories,
the `STEPUP_ROOT` directory will automatically be set correctly.
