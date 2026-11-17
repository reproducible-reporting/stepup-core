<!--
SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
SPDX-License-Identifier: LGPL-3.0-or-later
-->

# StepUp Core

[![release](https://github.com/reproducible-reporting/stepup-core/actions/workflows/release.yaml/badge.svg?branch=main)](https://github.com/reproducible-reporting/stepup-core/actions/workflows/release.yaml)
[![pytest](https://github.com/reproducible-reporting/stepup-core/actions/workflows/pytest.yaml/badge.svg?branch=main)](https://github.com/reproducible-reporting/stepup-core/actions/workflows/pytest.yaml)
[![docs](https://github.com/reproducible-reporting/stepup-core/actions/workflows/docs.yaml/badge.svg?branch=main)](https://github.com/reproducible-reporting/stepup-core/actions/workflows/docs.yaml)
[![PyPI Version](https://img.shields.io/pypi/v/stepup)](https://pypi.org/project/stepup/)
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.22018869.svg)](https://doi.org/10.5281/zenodo.22018869)
![PyPI - Python Version](https://img.shields.io/pypi/pyversions/stepup)
![LGPL-3 License](https://img.shields.io/badge/License-LGPL_v3-blue.svg)
[![CodeFactor](https://www.codefactor.io/repository/github/reproducible-reporting/stepup-core/badge)](https://www.codefactor.io/repository/github/reproducible-reporting/stepup-core)

<!-- snipwise.md BEGIN abstract -->
StepUp is a dynamic build tool for workflows written in Python.
Like [Make](https://en.wikipedia.org/wiki/Make_(software)),
it reruns only the steps whose inputs have changed.
Unlike most build tools, StepUp starts executing steps with only partial knowledge of the workflow.
A platform-independent `plan.py` script defines the initial steps,
and while the workflow runs, any step can add more steps and dependencies,
based on the outputs built so far.
Workflows can thus grow with your work,
without having to fit your ideas into a rigid framework.
StepUp is therefore ideal for workflows that are less structured than software builds,
such as those that produce scientific publications or datasets.
<!-- snipwise.md END abstract -->

StepUp Core provides the basic framework for StepUp, without any domain-specific features.
Those live in extension packages:

- [StepUp RepRep](https://reproducible-reporting.github.io/stepup-reprep/)
  for creating **rep**roducible **rep**orts: papers, presentations, theses, etc.
- [StepUp Queue](https://reproducible-reporting.github.io/stepup-queue/)
  submits jobs to a SLURM scheduler.

## Installation

```bash
pip install stepup
```

See the [installation guide](https://reproducible-reporting.github.io/stepup-core/installation/)
for details.

## Quick Visual Impression

[![asciicast](https://asciinema.org/a/pJXDBvCXT9ndHDuo.svg)](https://asciinema.org/a/pJXDBvCXT9ndHDuo)

## Documentation

Full documentation, including a tutorial and a feature overview,
is available at <https://reproducible-reporting.github.io/stepup-core>.

## License

StepUp Core is distributed under the terms of the
[LGPL-3.0-or-later](https://github.com/reproducible-reporting/stepup-core/blob/main/LICENSE) license.
Contributions are welcome;
see the [development guide](https://reproducible-reporting.github.io/stepup-core/development/)
to get started.
