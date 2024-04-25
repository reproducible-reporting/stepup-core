# Installation

Requirements:

- [POSIX](https://en.wikipedia.org/wiki/POSIX) operating system: Linux, macOS or WSL. StepUp cannot not run natively on Windows.
- [Python](https://www.python.org/) ≥ 3.11
- [Pip](https://pip.pypa.io/)

It is assumed you know how to use [Pip](https://pip.pypa.io/).
We recommend to perform the installation in a [Python virtual environment](https://docs.python.org/3/library/venv.html) and to activate such environments with [direnv](https://direnv.net/).

The core package is installed with

```bash
pip install stepup
```

The RepRep extension (for reproducible reporting) is installed with:

```bash
pip install stepup-reprep
```
