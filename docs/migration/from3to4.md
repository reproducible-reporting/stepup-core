# Migration from StepUp 3.X to 4.X

A few things have changed in StepUp 4 that might require changes to your `plan.py` file.
These changes reflect optimizations of StepUp's internals or
get rid of poor historical API design choices.

## Directory Handling

In StepUp 3, directories were stored in the database and had to be created explicitly using `mkdir()`
or made static with `static()` or `glob()`.
In StepUp 4, directories are no longer stored in the database (except for static roots, see below).
Instead, they are created when needed.
This has a few practical consequences for your `plan.py` file:

- `mkdir()` is no longer needed and has been removed.

- When `static()` is called with a directory path, this has a different meaning than before.
  In StepUp 3, this just made the directory static.
  In StepUp 4, this makes all contained files (recursively) static.
  This implementation is *lazy*, meaning that the directory is not scanned immediately,
  but that contained files only become static when they are used as inputs.

- When `glob()` is called with a directory argument, an error is raised.

- The `_defer=True` argument to `glob()` is no longer supported.
  Use `static()` with a directory path instead, which has a similar effect.
  (Deferred globbing was slightly more flexible,
  but is now abandoned due to subtle and difficult to solve bugs.)

- Directories can no longer be used as inputs or outputs of steps.

## Resource constraints (replacement for pools and blocked steps)

- The `pool()` function has been removed, and pools can no longer be defined in `plan.py`.
  Instead, you can declare the resources available on the host via an environment variable,
  e.g. `STEPUP_RESOURCES="gpu:2,cpu:16"` to indicate that the host has two GPUs and 16 CPU cores.
  When defining steps, you can then specify the required resources, e.g., `resources="gpu:1,cpu:4"`,
  and StepUp will ensure that the available resources are not over-committed.
  You can override the available resources with the
  `--resources` command-line argument to `stepup boot` if needed.

  Note that the resource names are user-specified strings and StepUp does not implement
  pre-defined resource types, such as `gpu` or `cpu`.
  These resource definitions are only used to impose constraints when deciding which steps to run.
  You could equally use `foo` and `bar` in this example and obtain exactly the same effect.

- The `block=True` argument to `step()` and
  higher-level step-generating API functions has been removed.
  Instead, use the `resources` argument with a resource that is not available on the host,
  which will have the same effect, e.g. `resources="blocked"`.

## Abandoned Features

The following were practically unused and have been removed:

- The `_required=True` argument to `glob()`.
  In the rare cases that it is useful, it can be implemented with a simple check in the `plan.py` file.
