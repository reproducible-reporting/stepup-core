# Introduction

StepUp Core is a build tool without domain-specific features.
It can be extended easily to support domain-specific build featuers.
At the moment, there is one main extension, namely [StepUp RepRep](https://reproducible-reporting.github.io/stepup-reprep/).

This section discusses how to create a new extension package for StepUp Core.
In a nutshell, you can create a new Python package, optionally using the same `stepup` namespace.
In this package, there are three types of components you can implement:

1. **API functions** to send new steps (and other information) to the StepUp director process.

    > These are usually making use of the [Basic (and Composite) API functions](../reference/stepup.core.api.md)
    > in the `stepup.core.api` module.
    > An example of this is the [`runsh()`][stepup.core.api.runsh] function,
    > which is used to run a shell command.

2. **An action**, which implements the execution of a step.

    > For example, the `runsh` action in StepUp Core implements the execution of a shell command.

3. **A tool**, which appears as a new subcommand in the StepUp CLI.

    > An example tool in StepUp Core is `stepup clean`

Note that actions and API functions can have the same name, but they are different things.
The action is executed when the step is executed,
while the API function is used to define the step and send it to the director process.
