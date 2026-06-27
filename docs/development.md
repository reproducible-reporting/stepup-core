# Developer Notes

If you would like to contribute, please read [CONTRIBUTING.md](https://github.com/reproducible-reporting/.github/blob/main/CONTRIBUTING.md).

## Development Environment

If you break your development environment, you can discard it
by running `git clean -dfX` in the project root and repeating the instructions below.

We use [uv](https://docs.astral.sh/uv/) to manage the development environment.
Install it by following the [uv installation instructions](https://docs.astral.sh/uv/getting-started/installation/).

A local installation for testing and development can be set up
using the following commands:

```bash
git clone git@github.com:reproducible-reporting/stepup-core.git
cd stepup-core
uv sync --extra dev
pre-commit install
```

`uv sync` creates a virtual environment in `.venv`
and installs StepUp Core in editable mode together with its development dependencies.

Put the following lines in `.envrc`:

```bash
source .venv/bin/activate
export XDG_CACHE_HOME="${VIRTUAL_ENV}/cache"
export STEPUP_DEBUG="1"
export STEPUP_BUILD_DURATION="0"
export STEPUP_SYNC_RPC_TIMEOUT="30"
```

Finally, activate the environment:

```bash
direnv allow
```

Alternatively, you can prefix commands with `uv run` (e.g. `uv run pytest`)
instead of activating the virtual environment.

Note that `uv.lock` is not committed to the repo.
For development and CI, the latest versions of dependencies are used instead of some locked versions.

## Tests

We use pytest, so you can run the tests as follows:

```bash
pytest -vv
```

## Documentation

The documentation is created using [MkDocs](https://www.mkdocs.org/).
[mike](https://github.com/jimporter/mike) is used to manage documentation of different versions

Edit the documentation Markdown files with a live preview by running:

```bash
mkdocs serve
```

(Keep this running.)
Then open the live preview in your browser at [http://127.0.0.1:8000/](http://127.0.0.1:8000/)
and edit Markdown files in your IDE.

Please use [Semantic Line Breaks](https://sembr.org/)
because it results in cleaner file diffs when editing documentation.

## Tutorial Example Outputs

If you wish to regenerate the output of the examples, run `sb` in the `docs` directory:

```bash
cd docs
sb
```

Note that some scripts use [Graphviz](https://graphviz.org/) to generate diagrams,
so you must have it installed on your system.

## How to Make a Release

- Mark the release in `docs/changelog.md`.
  Do not forget to extend the links at the bottom of the file.
- Make a new commit and tag it with `vX.Y.Z`.
- Trigger the PyPI GitHub Action: `git push origin main --tags`.

## Profiling

StepUp has built-in support for profiling the director process with
the Linux `perf` profiler and the Yappi profiler.
These instructions assume you are running on Linux and have the `perf` userspace tool and/or Yappi installed.

- To profile with [`perf` on Linux](https://perfwiki.github.io/main/),
  set the `STEPUP_BUILD_PERF` environment variable to a frequency in Hz,
  or pass the frequency with the `--perf` command-line option.

    `perf`-based profiling requires a Linux system with the `perf` tool available,
    sufficient kernel permissions (for example, appropriate `perf_event_paranoid` settings
    or `CAP_PERFMON` capabilities), and a CPython build that supports `perf` trampolines as described
    in the [Python `perf` profiling documentation](https://docs.python.org/3/howto/perf_profiling.html).
    Support for `perf` was added in CPython 3.12.

    Enabling `perf` will result in a file `.stepup/perf.data` containing the profiling data.
    Use `perf script` to convert this file to a human-readable format:

    ```bash
    perf script -i .stepup/perf.data > perf.txt
    ```

    The resulting `perf.txt` file can be analyzed with tools like [Speedscope](https://www.speedscope.app/).

- To profile with [Yappi](https://github.com/sumerc/yappi),
  set the `STEPUP_BUILD_YAPPI` environment variable to `1` or `yes`,
  or pass `--yappi` on the command line.
  This will result in a file `.stepup/director.prof` containing the profiling data.
  This file can be analyzed with tools like [SnakeViz](https://jiffyclub.github.io/snakeviz/):

    ```bash
    uv pip install snakeviz
    snakeviz .stepup/director.prof
    ```
