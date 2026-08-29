<!--
SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# Contributing to StepUp Core

Contributions are welcome, from bug reports and documentation fixes to new features.

## Reporting Bugs and Requesting Features

Open an [issue](https://github.com/reproducible-reporting/stepup-core/issues).
For a bug, please include the StepUp version, the Python version, the operating system,
and a minimal example that reproduces the problem.

## Setting up a Development Environment

The [developer notes](https://reproducible-reporting.github.io/stepup-core/development/)
explain how to install the development environment with
[uv](https://docs.astral.sh/uv/) and [pre-commit](https://pre-commit.com/),
how to run the tests, and how to build the documentation locally.

## Making Changes

- Discuss larger changes in an issue before you start working on them,
  so that you do not invest time in an approach that turns out not to fit.
- Install the pre-commit hooks with `pre-commit install`.
  They format and lint every commit,
  and the same checks run in continuous integration.
- Add tests for the behaviour you change, and make sure `pytest` passes.
- Wrap English text with [Semantic Line Breaks](https://sembr.org/),
  because it keeps the diffs of prose small.
- Add an entry to `docs/changelog.md` when your change is visible to users.

## Pull Requests

Open a pull request against the `main` branch
and describe what the change does and why it is needed.
The general conventions of the Reproducible Reporting organization are documented in
the [organization contributing guide](https://github.com/reproducible-reporting/.github/blob/main/CONTRIBUTING.md).

By contributing, you agree that your contributions are licensed under the
[LGPL-3.0-or-later](https://github.com/reproducible-reporting/stepup-core/blob/main/LICENSE)
license.
