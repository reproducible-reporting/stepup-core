# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

StepUp Core is a dynamic build tool implemented in Python.
It runs a persistent **director** process that manages a workflow graph (stored in SQLite),
dispatches jobs to a **step executor**,
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
After making code changes, run all pre-commit checks before considering the work done:

```bash
pre-commit run --all
```

To run individual linters manually:

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

#### Documentation examples

Each `docs/getting_started/<example>/` directory contains a `main.sh`
that generates `stdout.txt` (the terminal output shown in the tutorial page).
To regenerate after changing example scripts, run:

```bash
cd docs/getting_started/<example>
bash main.sh
```

This runs StepUp locally and captures the output via `sed -f ../../clean_stdout.sed`.
Commit the updated `stdout.txt` alongside any source changes.

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
- Use the imperative mood for function descriptions
  (e.g., "Compute the hash of a file."),
  except for `@property` getters where the description should be a noun phrase
  (e.g., "The parent directory path.").
- Do not repeat type annotations in the docstring — they are already in the function signature.
- In `Parameters` sections, use the **parameter name** as the heading for each parameter,
  not the type. Grouping closely related parameters under a combined heading
  (e.g., `stdout, stderr`) is allowed when the mkdocs rendering supports it and
  the parameters share the same description.

   ```python
    In `Returns` sections, use a **semantic name** for the return value, not the type:

    ```python
    # correct
    Returns
    -------
    parent
        The parent directory path.

    # wrong — the type is already in the signature
    Returns
    -------
    Path
        The parent directory path.
    ```

### Markdown

The project uses markdownlint (via pre-commit) on all `.md` files.
Two rules that are easy to get wrong:

- **MD007** — nested list items must use **4-space** indentation, not 2-space.
  Match the pattern already in use throughout the repo:

  ```markdown
  - Top-level item
      - Nested item (4 spaces)
  ```

- **MD031** — fenced code blocks must have a **blank line** before and after them,
  even when they appear inside a list item.

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
  Manages `Builder`, `Watcher`, and `Scheduler`.
  Steps run *inside* the director's event loop as asyncio tasks.
- **Executor** (`executor.py`):
  Runs each step as an asyncio task. Commands run as asyncio subprocesses (shell / direct
  exec) or in a forkserver child (Python scripts and console-script entry points).
  A single `Executor` instance serves all concurrent steps; `--jobs` is the
  concurrency limit. Step child processes call back into the director over its RPC socket
  (e.g. `amend()`, `step()`).
- **Hashing** (`hasher.py`):
  File/step hashing — the only blocking work — is offloaded to a separate process: a
  forkserver child when `--fork-runpy` is on, otherwise a `_stepup_hasher` subprocess.
- **TUI** (`tui.py`):
  Boots the director as a subprocess and connects to it via the reporter RPC socket.
  Renders progress to the terminal.

The entry point `stepup build` (in `tui.py`) is what users run.
It spawns the director and connects to it.

### Workflow Graph (`trellis.py`, `workflow.py`)

The core data structure is a combined **provenance** and **dependency** graph stored in SQLite.
`Trellis` (in `trellis.py`) is the abstract base implementing the graph, leveraging recursive SQL.
`Workflow` (in `workflow.py`) extends it with concrete node types:

- **`File`** (`file.py`):
  Tracks files with states `STATIC | AWAITED | BUILT | OUTDATED | MISSING | VOLATILE`.
- **`Step`** (`step.py`):
  A build step (command + inputs/outputs). States: `PENDING | RUNNING | SUCCEEDED | FAILED`.
- **`StaticTree`** (`static_tree.py`):
  Static tree node, used for inputs that are automatically declared as static (e.g., source files).

All graph mutations happen inside SQLite transactions.
The `DBSession` in `utils.py` serializes writes.

#### Database schema versioning

The schema version is `Trellis.schema_version` (in `trellis.py`), written to the database via
`PRAGMA user_version`. On a version mismatch, the database is **wiped and recreated** from
scratch (`wipe_database`) — there is no `ALTER TABLE` migration path.

**Policy: bump `schema_version` at most once per release.**
During a pre-release refactor, many commits may change the schema,
but they all share the single bumped version for the upcoming release;
do not bump the version again within the same release cycle.
Record each individual schema change as a comment line in the `schema_version` docstring,
even when the number itself does not change.

### RPC Layer (`rpc.py`)

Lightweight pickle-based RPC over asyncio streams or Unix sockets.
Methods decorated with `@allow_rpc` are exposed remotely.
Both sync (`SocketSyncRPCClient`) and async (`AsyncRPCClient`) clients exist.
The director runs a socket RPC server; step child processes and the TUI are the clients.

### File path considerations

StepUp uses the `path` module instead of the built-in `pathlib` to handle file paths.
In some cases, path affixes must be preserved (leading `./` or trailing `/`),
which `pathlib` normalizes away. The `path` module preserves these affixes.

The affixes are currently used in the places in StepUp:

- The `dst` argument of the `copy()` function in `stepup.core.api`, with
  a reusable mechanism for output path construction in `make_path_out()` in `stepup.core.utils`.
- A local executable must contain at least one slash, e.g., `./script.sh` or `bin/script.sh`.
- The `getenv()` function in `stepup.core.utils` preserves path affixes
  when reading environment variables must be treated as paths.
- A static tree path in the database is always stored with a trailing slash.

The `get_affixes()` and `apply_affixes()` functions in `stepup.core.utils` are used to
extract and re-apply the affixes when needed.

### User-Facing API (`api.py`)

`plan.py` scripts call functions in `api.py` (e.g., `static()`, `step()`, `glob()`)
which send RPC calls to the director.
The module must not be imported by other `stepup.core` modules
except `interact.py`, `call.py`, `script.py`, and `extapi.py` (local imports only).

### Extension Developer API (`extapi.py`)

`extapi.py` collects utilities for authors of StepUp extension packages:
`subs_env_vars`, `get_rpc_client`, `filter_dependencies`, and `get_local_import_paths`.
These are re-exported from `api.py` and `utils.py` for backward compatibility.
`extapi.py` imports from `api.py` only via local (inside-function) imports to avoid
circular dependencies at module load time.

### Step Execution Pipeline

1. `Scheduler` (`scheduler.py`) picks the highest-priority runnable step
   from the `Workflow` and creates a corresponding `Job` instance.
2. `Builder` (`builder.py`) requests a runnable job from the scheduler and, up to the
   concurrency limit, starts it as an asyncio task on the shared `Executor`.
3. `Executor` (`executor.py`) runs the step's command (subprocess or forkserver child),
   which may produce more RPC calls back to the director.
4. The executor computes file hashes in a separate process and updates
   `FileState` and `StepState` in the workflow.

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
  and how the test builder compares `current_*` files against `expected_*` files.
    - New examples are **not** auto-discovered: register each in the `@pytest.mark.parametrize`
      `name` list of `test_example` in `tests/test_examples.py` (and `test_plan` if the plan
      should run standalone), or it is silently never run.
    - CI runs the example suite twice, with `STEPUP_BUILD_FORK_RUNPY=1` and `=0`, so examples
      must pass under both the forkserver and plain-subprocess paths.
    - The "Standard error" page is replaced with `(stripped)` before comparison, so assert
      stderr text by grepping `.stepup/success.log` (full output) instead of `expected_stdout.txt`.
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

Always wrap the quick test run in a short timeout, e.g.:

```bash
timeout 15 pytest -k "not test_example"
```

15 seconds is very generous for this selection.
If a step crashes, the `client` test fixture can otherwise block indefinitely
(it waits for the workflow to reach a state that never arrives),
so a timeout prevents a runaway, hanging test process.

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
