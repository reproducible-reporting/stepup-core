# Introduction

StepUp Core is a build tool without domain-specific features.
It can be easily extended to support domain-specific build features.
Currently, there is one major extension, [StepUp RepRep](https://reproducible-reporting.github.io/stepup-reprep/).

This section discusses how to create a new extension package for StepUp Core.
In short, you can create a new Python package, optionally using the same `stepup` namespace.
There are two types of components you can implement in an extension package:

1. **API functions** to send new steps (and other information) to the StepUp director process.

    > These are typically making use of the [Basic (and Composite) API functions](../reference/stepup.core.api.md)
    > in the `stepup.core.api` module.
    > An example of this is the [`run()`][stepup.core.api.run] function,
    > which is used to execute commands.

2. **A tool**, which appears as a new subcommand in the StepUp CLI.

    > An example tool in StepUp Core is `stepup clean`.
