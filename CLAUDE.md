# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

StepUp Core is a dynamic build tool implemented in Python.
It runs a persistent **director** process that manages a workflow graph (stored in SQLite),
dispatches jobs to **worker** processes,
and reacts to file system changes (via inotify) to re-run only outdated steps.

## Commands

### Setup

```bash
python -m venv venv
source venv/bin/activate          # or: direnv allow (uses .envrc)
pip install -e .[dev]
pre-commit install
```

The `.envrc` sets `STEPUP_DEBUG=1`, `STEPUP_DURATION=0`,
and `STEPUP_SYNC_RPC_TIMEOUT=30` for development.

### Linting

Pre-commit hooks run `ruff format` and `ruff check` automatically on commit.
To run manually:

```bash
ruff format stepup/ tests/
ruff check --fix stepup/ tests/
```

### Tests

```bash
pytest -vv                        # all tests (parallel by default via pytest-xdist)
pytest -vv tests/test_api.py      # single file
pytest -vv -k "test_name"         # single test by name
```

Tests default to `-n auto --dist worksteal` (parallel).
The test suite uses `pytest-asyncio` for async tests.

### Documentation

```bash
mkdocs serve                      # live preview at http://127.0.0.1:8000/
```

Note that docstrings are written in Markdown, not reStructuredText!

## Coding Conventions

### Linting (ruff)

Ruff's rule selection is configured in `pyproject.toml` under `[tool.ruff.lint]`.
Do not add `# noqa` comments unless the violation is a genuine false positive that cannot
be resolved by restructuring the code — the project's rule set already excludes rules
that would fire spuriously in this codebase.

Key rules to be aware of:

- The default line length is 100.

### Docstrings

Use **NumPy-style** sections (`Parameters`, `Returns`, `Raises`, ...)
Some conventions specific to this codebase:

- Docstrings are written in Markdown, not reStructuredText! Some important gotcha's:
    - Use `**bold**` for emphasis, not `*italics*` (which is reserved for parameter names).
    - Use single backticks for inline code and parameter names, not double backticks.
    - Use triple backticks for code blocks,
      and specify the language for syntax highlighting (e.g., ```python).
- Wrap lines using semantic breaks (e.g., after sentences or logical units),
  not hard-wrapping at a specific character limit.
  See <https://sembr.org/>
- Use the imperative mood for function descriptions (e.g., "Compute the hash of a file.")
- Do not repeat type annotations in the docstring — they are already in the function signature.
- In `Parameters` sections, use the **parameter name** as the heading for each parameter,
  not the type:

   ```python
    In `Returns` sections, use a **semantic name** for the return value, not the type:

    ```python
    # correct
    Returns
    -------
    parent
        The parent directory path with a trailing slash.

    # wrong — the type is already in the signature
    Returns
    -------
    Path
        The parent directory path.
    ```

### Dependencies

Runtime dependencies are declared in `pyproject.toml` under `[project] dependencies`.
Before adding a lazy import or a try/except ImportError guard, check whether the package
is already a declared dependency and import it at the top of the file instead.

## Architecture

### Process Model

StepUp runs as two process types:

- **Director** (`director.py`):
  An asyncio process that owns the workflow graph and SQLite database.
  It exposes an RPC server over a Unix socket (`.stepup/sockets/director`).
  Manages `Runner`, `Watcher`, and `Dispatcher`.
- **Worker** (`worker.py`):
  Subprocess(es) that execute individual steps.
  Each worker connects back to the director via its own RPC socket.
  Workers report file hashes, step outcomes, and newly declared steps.
- **TUI** (`tui.py`):
  Boots the director as a subprocess and connects to it via the reporter RPC socket.
  Renders progress to the terminal.

The entry point `stepup boot` (in `tui.py`) is what users run.
It spawns the director and connects to it.

### Workflow Graph (`cascade.py`, `workflow.py`)

The core data structure is a combined **provenance** and **dependency** graph stored in SQLite.
`Cascade` (in `cascade.py`) is the abstract base implementing the graph, leveraging recursive SQL.
`Workflow` (in `workflow.py`) extends it with concrete node types:

- **`File`** (`file.py`):
  Tracks files with states `STATIC | AWAITED | BUILT | OUTDATED | MISSING | VOLATILE`.
- **`Step`** (`step.py`):
  A build step (command + inputs/outputs). States: `PENDING | RUNNING | SUCCEEDED | FAILED`.
- **`StaticRoot`** (`static_root.py`):
  Static root node, used for inputs that are automatically declared as static (e.g., source files).

All graph mutations happen inside SQLite transactions.
The `DBLock` in `utils.py` serializes writes.

### RPC Layer (`rpc.py`)

Lightweight pickle-based RPC over asyncio streams or Unix sockets.
Methods decorated with `@allow_rpc` are exposed remotely.
Both sync (`SocketSyncRPCClient`) and async (`AsyncRPCClient`) clients exist.
Workers use stdio RPC; the director uses socket RPC.

### User-Facing API (`api.py`)

`plan.py` scripts call functions in `api.py` (e.g., `static()`, `step()`, `glob()`)
which send RPC calls to the director.
The module must not be imported by other `stepup.core` modules
except `interact.py`, `call.py`, and `script.py`.

### Step Execution Pipeline

1. `Dispatcher` (`dispatcher.py`) picks the highest-priority runnable step
   from the `Workflow` and creates a corresponding `Job` instance.
2. `Runner` (`runner.py`) requests a runnable job from the dispatcher.
3. `Worker` processes execute steps, which may produce more RPC calls to the director.
4. On completion, the worker reports file hashes back to the director.
   The director updates `FileState` and `StepState` in the workflow.

### Named Globs (`nglob.py`)

`NGlobSingle` / `NGlobMulti` implement pattern matching with named back-references (`${*name}`).
Used in the API for dynamic file discovery with consistency constraints across patterns.

### Key Environment Variables

| Variable | Purpose |
| --- | --- |
| `STEPUP_DEBUG` | Enable debug logging |
| `STEPUP_DURATION` | Measure step durations to optimize scheduling (set `0` to disable in tests) |
| `STEPUP_SYNC_RPC_TIMEOUT` | Timeout for sync RPC calls (seconds) |
| `STEPUP_PERF` | Frequency (Hz) for Linux `perf` profiling of director |
| `STEPUP_YAPPI` | Enable Yappi profiling of director |

### Test Structure

- `tests/conftest.py` defines fixtures:
    - `wfs` (bare workflow)
    - `wfp` (workflow with plan.py),
    - `client` (full director running in-process).
- `tests/examples/*/` contains integration test cases,
  each with `plan.py`, `main.sh`, and `expected_stdout*.txt` / `expected_graph*.txt`.
  These are run by `tests/test_examples.py`.
  See `tests/examples/README.md` for a detailed explanation of the `main.sh` conventions
  and how the test runner compares `current_*` files against `expected_*` files.
- `stepup/core/pytest.py`:
  Pytest helpers for integration tests that run actual StepUp workflows.
- To regenerate `expected_*` files after an intentional behavior change, run:

  ```bash
  STEPUP_OVERWRITE_EXPECTED=1 pytest tests/test_examples.py
  ```

  Review the diffs with `git diff` afterwards to confirm only expected changes.

### Test instructions

The following test commands will complete quickly as it skips the integration tests:

```bash
pytest -k "not test_example"
```

It may also be useful to run a small number of integration tests,
to get a first quick feedback on the overall system:

```bash
pytest tests/test_examples.py -k "test_example[no_static] or test_example[restart_add_missing]"
```

These are two simple examples that run quickly and will fail when the core system is broken.
A full run with all integration tests takes several minutes and is best run as a final check only.

### Release Process

1. Update `docs/changelog.md`.
2. Commit and tag: `git tag vX.Y.Z`.
3. Push with tags: `git push origin main --tags` — triggers PyPI GitHub Action.
