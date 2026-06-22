# Introduction

StepUp Core can be easily extended to support domain-specific build features,
which are not part of the core functionality.
This is done by creating a new Python package that implements the desired features
and registers them with StepUp Core.
Currently, there are two such extensions:

- [StepUp RepRep](https://reproducible-reporting.github.io/stepup-reprep/)
- [StepUp Queue](https://reproducible-reporting.github.io/stepup-queue/)

This section discusses how to create a new extension package for StepUp Core.
In short, you can create a new Python package, optionally using the same `stepup` namespace.
There are two types of components you can implement in such an extension package:

1. **API functions** to send new steps (and other information) to the StepUp director process.

    > These are typically making use of the
    > [Basic (and Composite) API functions](../reference/stepup.core.api.md)
    > in the `stepup.core.api` module.
    > An example of this is the [`run()`][stepup.core.api.run] function,
    > which is used to execute commands.
    >
    > Extension developers should also consult the
    > [`stepup.core.extapi`](../reference/stepup.core.extapi.md) module,
    > which provides utilities for building custom API functions,
    > such as [`subs_env_vars()`][stepup.core.extapi.subs_env_vars] for environment variable
    > substitution and [`filter_dependencies()`][stepup.core.extapi.filter_dependencies]
    > for step dependency filtering.
    >
    > The module [`stepup.core.utils`](../reference/stepup.core.utils.md) also provides
    > some convenience functions for extension developers.

2. **A tool**, which appears as a new subcommand in the StepUp CLI.

    > An example tool in StepUp Core is `stepup clean`.
