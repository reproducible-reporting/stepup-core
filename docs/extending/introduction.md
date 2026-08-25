# Introduction

<!--
SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
SPDX-License-Identifier: CC-BY-SA-4.0
-->

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

## Testing an Extension

The module `stepup.core.pytest` holds test utilities that extensions can reuse:

- `run_example()` runs an integration example in a temporary directory
  and compares the files it produces to the expected ones in the source directory.
  `run_plan()` runs the `plan.py` of an example as an ordinary Python script.
- `ConventionTests` collects the tests for the coding conventions of the StepUp packages,
  most importantly that every module declares `__all__`
  and that no module imports a name another module does not export.
  Subclass it in a test module and name the package to be tested:

    ```python
    from stepup.core.pytest import ConventionTests


    class TestConventions(ConventionTests):
        package = "stepup.spam"
    ```

    Set `example_rc = None` in the subclass when the test suite has no integration examples.
    For the failing assertions to explain themselves,
    add the following line to `conftest.py`:

    ```python
    pytest.register_assert_rewrite("stepup.core.pytest")
    ```
