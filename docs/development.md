# Developer Notes

If you would like to contribute, please read [CONTRIBUTING.md](https://github.com/reproducible-reporting/.github/blob/main/CONTRIBUTING.md).

## Development environment

If you break your development environment, you can discard it
by running `git clean -dfX` and repeating the instructions below.

A local installation for testing and development can be installed
using the following commands:

```bash
git clone git@github.com:reproducible-reporting/stepup-core.git
cd stepup-core
pre-commit install
python -m venv venv
```

Put the following lines in `.envrc`:

```bash
source venv/bin/activate
export XDG_CACHE_HOME="${VIRTUAL_ENV}/cache"
export STEPUP_DEBUG="1"
export STEPUP_SYNC_RPC_TIMEOUT="30"
```

Finally, run the following commands:

```bash
direnv allow
pip install -U pip
pip install -e .[dev]
```

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

Please, use [Semantic Line Breaks](https://sembr.org/)
because it facilitates reviewing documentation changes.

## Tutorial Example Outputs

If you wish to regenerate the output of the examples, run `stepup` in the `docs` directory:

```bash
cd docs
stepup
```

(Keep this running.)
Then open the live preview in your browser: [http://127.0.0.1:8000/](http://127.0.0.1:8000/)
and edit Markdown files in your IDE.

Please, use [Semantic Line Breaks](https://sembr.org/)
because it results in cleaner file diffs when editing documentation.
Note that some scripts use [Graphviz](https://graphviz.org/) to generate diagrams,
so you must have it installed on your system.

## How to Make a Release

- Mark the release in `docs/changelog.md`.
  Do not forget to extend the links at the bottom of the file.
- Make a new commit and tag it with `vX.Y.Z`.
- Trigger the PyPI GitHub Action: `git push origin main --tags`.
