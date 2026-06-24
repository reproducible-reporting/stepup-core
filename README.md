<!-- markdownlint-disable line-length -->

# StepUp Core

[![release](https://github.com/reproducible-reporting/stepup-core/actions/workflows/release.yaml/badge.svg?branch=main)](https://github.com/reproducible-reporting/stepup-core/actions/workflows/release.yaml)
[![pytest](https://github.com/reproducible-reporting/stepup-core/actions/workflows/pytest.yaml/badge.svg?branch=main)](https://github.com/reproducible-reporting/stepup-core/actions/workflows/pytest.yaml)
[![mkdocs](https://github.com/reproducible-reporting/stepup-core/actions/workflows/mkdocs.yaml/badge.svg?branch=main)](https://github.com/reproducible-reporting/stepup-core/actions/workflows/mkdocs.yaml)
[![PyPI Version](https://img.shields.io/pypi/v/stepup)](https://pypi.org/project/stepup/)
![PyPI - Python Version](https://img.shields.io/pypi/pyversions/stepup)
![GPL-3 License](https://img.shields.io/github/license/reproducible-reporting/stepup-core)
[![CodeFactor](https://www.codefactor.io/repository/github/reproducible-reporting/stepup-core/badge)](https://www.codefactor.io/repository/github/reproducible-reporting/stepup-core)

StepUp is a simple, powerful and universal build tool,
a modern alternative to [Make](https://en.wikipedia.org/wiki/Make_(software)).
Its defining feature is that it treats the generation and execution of the build workflow
as one and the same thing:
a `plan.py` Python script issues build steps via RPC to a persistent background process,
so steps can be defined on the fly using the outputs of earlier steps.
This makes StepUp a good fit for builds where the full set of dependencies
cannot be known in advance.

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
[GPL-3.0-or-later](LICENSE) license.
Contributions are welcome;
see the [development guide](https://reproducible-reporting.github.io/stepup-core/development/)
to get started.
