# Developer notes

If you would like to contribute, please read [CONTRIBUTING.md](https://github.com/reproducible-reporting/.github/blob/main/CONTRIBUTING.md).

## How to make releases

- Mark release in `changelog.md`.
- Make a new commit and tag it with `vX.Y.Z`.
- Trigger the PyPI GitHub Action: `git push origin main --tags`.


## Local development installation

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
