# Custom API Functions

This is the most common way to extend StepUp Core.
It is essentially the same as writing functions
as those explained in the [No Rules](../getting_started/no_rules.md) tutorial.
The only difference is that you can include such functions in a Python package,
and call it a StepUp extension, which makes it easier to share and reuse these functions.

A few things to keep in mind:

- API functions that (indirectly) call the [`step()`][stepup.core.api.step] function
  should always return the resulting `StepInfo` object.
- Keep the computational cost of the API function low.
  They should only be used to plan the execution of a step
  and not perform any of the actual work.
- If the step runs a Python script or program, make sure you set `shell=False` in the `step()` call.
  StepUp will then run it in-process via the forkserver when available,
  without spawning a new Python interpreter.
  See [Command Dispatch](command.md) for details.

## Extension utilities

The [`stepup.core.extapi`](../reference/stepup.core.extapi.md) module provides utilities
specifically for extension developers.

### Environment variable substitution

Use [`subs_env_vars()`][stepup.core.extapi.subs_env_vars] in any custom API function that
accepts path arguments that may contain shell variable references (e.g. `${MYDIR}/file.txt`).
It yields a `subs` function that performs the substitution and automatically tracks which
variables were used, amending the current step's environment dependencies accordingly:

```python
from stepup.core.extapi import subs_env_vars

def my_api_function(path_inp, path_out):
    with subs_env_vars() as subs:
        path_inp = subs(path_inp)
        path_out = subs(path_out)
    # path_inp and path_out are now resolved Path objects
```

### Dependency filtering

[`filter_dependencies()`][stepup.core.extapi.filter_dependencies] filters a collection of
file paths according to the `${STEPUP_PATH_FILTER}` environment variable.
It is useful when a step dynamically discovers which local files it depends on at runtime,
and needs to report only the relevant subset back to the director.
[`get_local_import_paths()`][stepup.core.extapi.get_local_import_paths] builds on this to
return all Python modules currently loaded in `sys.modules` that pass the filter —
useful for scripts that need to amend their Python-import dependencies.
