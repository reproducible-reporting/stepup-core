# Introduction

StepUp Core supports domain-specific build features
through extension packages that live outside the core.
An extension is a regular Python package
that implements the desired features and registers them with StepUp Core,
optionally reusing the shared `stepup` namespace.
Two such extensions exist today:

- [StepUp RepRep](https://reproducible-reporting.github.io/stepup-reprep/)
- [StepUp Queue](https://reproducible-reporting.github.io/stepup-queue/)

This section explains how to write your own extension.
A package can implement three types of components:

1. [Custom **API functions**](api.md) that send new steps (and other information)
   to the StepUp director process.

2. [**Console scripts**](console_scripts.md), e.g. wrappers of external tools,
   intended to run as a step in a workflow.

3. [Custom **tools**](tool.md), which appear as new subcommands in the StepUp CLI.
