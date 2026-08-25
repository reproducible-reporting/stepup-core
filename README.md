<!--
SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
SPDX-License-Identifier: LGPL-3.0-or-later
-->

# StepUp Core

[![release](https://github.com/reproducible-reporting/stepup-core/actions/workflows/release.yaml/badge.svg?branch=main)](https://github.com/reproducible-reporting/stepup-core/actions/workflows/release.yaml)
[![pytest](https://github.com/reproducible-reporting/stepup-core/actions/workflows/pytest.yaml/badge.svg?branch=main)](https://github.com/reproducible-reporting/stepup-core/actions/workflows/pytest.yaml)
[![mkdocs](https://github.com/reproducible-reporting/stepup-core/actions/workflows/mkdocs.yaml/badge.svg?branch=main)](https://github.com/reproducible-reporting/stepup-core/actions/workflows/mkdocs.yaml)
[![PyPI Version](https://img.shields.io/pypi/v/stepup)](https://pypi.org/project/stepup/)
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.22018869.svg)](https://doi.org/10.5281/zenodo.22018869)
![PyPI - Python Version](https://img.shields.io/pypi/pyversions/stepup)
![LGPL-3 License](https://img.shields.io/badge/License-LGPL_v3-blue.svg)
[![CodeFactor](https://www.codefactor.io/repository/github/reproducible-reporting/stepup-core/badge)](https://www.codefactor.io/repository/github/reproducible-reporting/stepup-core)

StepUp is a dynamic build tool and a modern alternative to
[Make](https://en.wikipedia.org/wiki/Make_(software)).
Its defining feature is that workflow generation and execution are unified:
a `plan.py` script defines the initial build steps.
While the workflow is being executed, any step can add more steps and dependencies,
based on the outputs built so far.
This makes StepUp ideal for builds
where the full set of dependencies cannot be determined in advance.

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

[![asciicast](https://asciinema.org/a/718833.svg)](https://asciinema.org/a/718833)

## Documentation

Full documentation, including a tutorial and a feature overview,
is available at <https://reproducible-reporting.github.io/stepup-core>.

## License

StepUp Core is distributed under the terms of the
[LGPL-3.0-or-later](LICENSE) license.
Contributions are welcome;
see the [development guide](https://reproducible-reporting.github.io/stepup-core/development/)
to get started.
