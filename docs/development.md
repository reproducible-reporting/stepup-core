# Developer Notes

If you would like to contribute, please read [CONTRIBUTING.md](https://github.com/reproducible-reporting/.github/blob/main/CONTRIBUTING.md).


## Development Install and Unit Tests

A local installation for testing and development can be installed as follows

```bash
git clone git@github.com:reproducible-reporting/stepup-core.git
cd stepup-core
pre-commit install
python -m venv venv
echo 'source venv/bin/activate' > .envrc
direnv allow
pip install -U pip
pip install -e .[dev]
pytest -vv
```

## Documentation

The documentation is created using [MkDocs](https://www.mkdocs.org/).

Edit the documentation Markdown files with a live preview by running:

```bash
mkdocs serve --watch stepup/core/
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
stepup -n
```

Note that some scripts use [Graphviz](https://graphviz.org/) to generate diagrams,
so you must have it installed on your system.


## How to Make a Release

- Mark the release in `docs/changelog.md`.
- Make a new commit and tag it with `vX.Y.Z`.
- Trigger the PyPI GitHub Action: `git push origin main --tags`.
