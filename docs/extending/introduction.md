---
description: >-
  Learn what a StepUp extension package can add, namely API functions, console scripts
  and custom CLI tools, and how to test one with stepup.core.pytest.
---

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

A package that is configured through environment variables
also [registers these variables](env_vars.md),
so that `stepup config` recognizes them and never shows a secret.

## Testing an Extension

The module `stepup.core.pytest` holds test utilities that extensions can reuse:

- `run_example()` runs an integration example in a temporary directory
  and compares the files it produces to the expected ones in the source directory.
  `run_plan()` runs the `plan.py` of an example as an ordinary Python script.
- `OutputRule` tells `run_example()` how to clean up the outputs whose names match a pattern.
  It normalizes an output into the form that is stored in the expected file,
  e.g. by removing a date,
  and it masks what may only differ in some environments while comparing,
  e.g. file modes on a platform without permission bits.
  The first rule that matches an output applies.
  The default rules, `OUTPUT_RULES`, cover the output of `stepup build` and `stepup graph`.
  An extension with outputs of its own puts its rules in front of them,
  and passes `rules=RULES` to `run_example()`:

    ```python
    import re

    from stepup.core.pytest import OUTPUT_RULES, OutputRule


    def normalize_report(text: str) -> str:
        return re.sub(r"^Date: .*\n", "", text, flags=re.MULTILINE)


    RULES = (OutputRule("_report*.txt", normalize=normalize_report), *OUTPUT_RULES)
    ```

- `ConventionTests` collects the tests for the coding conventions of the StepUp packages,
  most importantly that every module declares `__all__`
  and that no module imports a name another module does not export.
  The tests cover the modules of subpackages as well.
  Subclass it in a test module and name the package to be tested:

    ```python
    from stepup.core.pytest import ConventionTests


    class TestConventions(ConventionTests):
        package = "stepup.spam"
    ```

    Set `example_rc = None` in the subclass when the test suite has no integration examples.
    Add the following line to `conftest.py`,
    so that the failing assertions explain themselves,
    and so that pytest reports the output of failed examples legibly on Windows:

    ```python
    pytest_plugins = ("stepup.core.pytest",)
    ```
