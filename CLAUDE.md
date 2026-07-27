<!--
SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
SPDX-License-Identifier: LGPL-3.0-or-later
-->
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
uv sync --extra dev
source .venv/bin/activate    # or: direnv allow (uses .envrc)
pre-commit install
```

The `.envrc` sets `STEPUP_DEBUG=1`, `STEPUP_BUILD_DURATION=0`,
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

### Semantic Line Breaks

All English text in this repo — comments (including SQL comments), docstrings,
Markdown documentation, commit messages, etc. — is wrapped using **semantic line breaks**:
break after sentences or logical units, not at a fixed character count.
See <https://sembr.org/>.
This makes diffs to prose easier to review,
since editing one sentence doesn't reflow unrelated lines.
The 100-character line length (see Linting below) is a hard cap, not a target to fill.

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
- Lines are wrapped using semantic breaks, per
  [Semantic Line Breaks](#semantic-line-breaks) above.
- Use the imperative mood for function descriptions
  (e.g., "Compute the hash of a file."),
  except for `@property` getters where the description should be a noun phrase
  (e.g., "The parent directory path.").
- Do not repeat type annotations in the docstring — they are already in the function signature.
- In `Parameters` sections, use the **parameter name** as the heading for each parameter,
  not the type. Grouping closely related parameters under a combined heading
  (e.g., `stdout, stderr`) is allowed when the mkdocs rendering supports it and
  the parameters share the same description.

- In `Returns` sections, use a **semantic name** for the return value, not the type:

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

Section headings (`##`, `###`, ...) use **Title Case**
(capitalize nouns, verbs, adjectives, and adverbs; lowercase articles,
coordinating conjunctions, and prepositions regardless of length, e.g. "from", "with").
Inline code spans (e.g. `` `run()` ``) keep their own casing and are never title-cased.

### `__all__`

Wildcard imports are banned (ruff `F403`), so `__all__` does not describe a star-import
surface here. It is the module's **import contract**: the names that code outside the module
is meant to import.

- Every module in `stepup/core/` declares `__all__`, placed directly after the imports and
  before `logger`. It is a tuple of string literals, sorted (enforced by ruff `RUF022`).
- List a name when it is imported by another `stepup` module, a downstream extension package,
  a user's `plan.py`, or a `pyproject.toml` entry point (e.g. `build_subcommand`).
- Do not list module-internal names, even when they lack a leading underscore:
  `logger`, SQL constants (`*_SCHEMA`, `SELECT_*`, ...), helpers used only within the module.
  A public-looking name is not a claim that the name is exported.
- Tests may import names that are not in `__all__`; white-box testing does not make a name
  part of the contract.
- Do not re-export: a name in `__all__` must be defined in that same module.
  Import a name from the module that defines it, not from a module that happens to import it.
- `__all__ = ()` is a real claim — nothing outside the module may import from it —
  and is correct only for leaf modules.

Consequence, enforced by `tests/test_conventions.py`: any `from .mod import X` inside
`stepup/` requires `X` to be in `mod.__all__`, with no exemption for underscore-prefixed names.
When a module needs something private from another one,
move that name to a module both may depend on (`utils.py` is often the right home),
or promote it to part of the defining module's contract.

### Dependencies

Runtime dependencies are declared in `pyproject.toml` under `[project] dependencies`.
Before adding a lazy import or a try/except ImportError guard, check whether the package
is already a declared dependency and import it at the top of the file instead.

## Architecture

### Process Model

StepUp runs as two process types:

- **Director** (`director.py`):
  An asyncio process that owns the workflow graph and SQLite database.
  It exposes an RPC server over a Unix socket, whose path is handed to it by the TUI
  (a per-run temp directory, e.g. `tempfile.TemporaryDirectory(prefix="stepup-")` — not under
  `.stepup/`).
  Manages `Builder`, `Watcher`, and `Scheduler`.
  Steps run *inside* the director's event loop as asyncio tasks.
- **Executor** (`executor.py`):
  Runs each step as an asyncio task, tying the step lifecycle (skip/run/postpone decisions,
  hash bookkeeping, reporting) together.
  A single `Executor` instance serves all concurrent steps; `--jobs` is the
  concurrency limit. Step child processes call back into the director over its RPC socket
  (e.g. `amend()`, `step()`).
  Launching the step's command and hashing its files are delegated to `run.py` and
  `hash.py` respectively (see below).
- **Hashing** (`hash.py`):
  File/step hashing — the only blocking work — runs in a dedicated `ThreadWorker`,
  one thread per hash computation inside the director process, see `run.py`.
  The chunked digest loop releases the GIL and checks a cancel event between 256 KiB chunks,
  so hashes are concurrent and promptly interruptible.
  The pure functions `compute_inp_hashes` / `compute_out_hashes` / `compute_both_hashes` are
  what actually runs inside a `ThreadWorker`; `Executor` wraps them via `_run_work_thread`.
- **TUI** (`tui.py`):
  Spawns the director as a subprocess and connects to its RPC socket as a client
  (e.g. to forward keyboard commands). It also serves the reporter RPC socket itself —
  the director connects to *that* as a client to report progress.
  Renders progress to the terminal.

The entry point `stepup build` (in `tui.py`) is what users run.
It spawns the director and connects to it.
(`stepup boot` still exists as a deprecated alias of `stepup build`.)

Naming gotcha — two similar flags with different meanings:

- `keep_going` (CLI `-k` / `--keep-going`, env `STEPUP_BUILD_KEEP_GOING`):
  keep building unrelated steps after a step has failed.
- `carry_on` (internal, part of the director's `amend()` RPC result):
  whether a running step may continue after amending its inputs,
  or must abort because some amended inputs are not yet available.

### Step Launching and Interruption (`run.py`)

`run.py` owns "run a step's command as a child process and return a `ChildOutcome`,"
independent of the step lifecycle in `executor.py`:
command classification (subshell vs. `*.py` script vs. console-script entry point vs. plain
exec), spawning the subprocess or forkserver child, and capturing its output/return code/
resource usage. `launch_command()` is its single dispatch entry point, called from
`Executor.run()`.

`run.py` also defines `Worker`, the base class for anything that is the in-flight work of a
`Run` and can be interrupted by `Executor.interrupt()`: `SubprocessWorker` and
`ForkserverWorker` signal the underlying OS process; `ThreadWorker`
cancels a computation instead, so it overrides `interrupt()` directly
rather than using `Worker`'s signal-delivery template.

Every step runs in a **session of its own**: `start_new_session=True` for subprocesses and
`os.setsid()` at the top of `_forkserver_entry` for forkserver children.
Both worker classes therefore signal a *process group* (`_signal_process_group`), not a single
pid, which is what reaches the actual work when a shell step is a pipeline or `&&`-chain that
keeps `sh` around as a wrapper.
The flip side is that the terminal no longer signals steps directly:
a Ctrl-C reaches only the TUI and the director, and the director is the only thing that stops
running steps (`DirectorHandler.interrupt`).

### Workflow Graph (`trellis.py`, `workflow.py`)

The core data structure is a combined **provenance** and **dependency** graph stored in SQLite.
`Trellis` (in `trellis.py`) is the abstract base implementing the graph, leveraging recursive SQL.
`Workflow` (in `workflow.py`) extends it with concrete node types:

- **`File`** (`file.py`):
  Tracks files with states
  `UNCONFIRMED | MISSING | STATIC | AWAITED | BUILT | OUTDATED | VOLATILE`.
- **`Step`** (`step.py`):
  A build step (command + inputs/outputs).
  States: `PENDING | RUNNING | CHECKING | SUCCEEDED | FAILED`.
- **`StaticTree`** (`static_tree.py`):
  Static tree node, used for inputs that are automatically declared as static (e.g., source files).

All graph mutations happen inside SQLite transactions.
The `DBSession` in `sqlite3.py` serializes writes.

#### Database schema versioning

The schema version is `Trellis.schema_version` (in `trellis.py`), written to the database via
`PRAGMA user_version`. On a version mismatch, the database is **wiped and recreated** from
scratch (`_wipe_database`) — there is no `ALTER TABLE` migration path.

Note that `DBSession.initialize()` re-executes the full schema (`CREATE TABLE IF NOT EXISTS`,
`CREATE INDEX IF NOT EXISTS`, ...) via `executescript` on **every** database open, regardless of
whether `user_version` matched. A purely additive change (e.g. a new index) is therefore applied
lazily even to a database whose `schema_version` didn't change — bumping the version is a
documentation/consistency convention for this project, not strictly required for such a change
to take effect.

**Policy: bump `schema_version` at most once per release.**
During a pre-release refactor, many commits may change the schema,
but they all share the single bumped version for the upcoming release;
do not bump the version again within the same release cycle.
Record each individual schema change as a comment line in the `schema_version` docstring,
even when the number itself does not change.

**Claude Code must never bump `schema_version`.**
Deciding when a release's schema changes are complete is a human judgment call,
so only a human coder bumps the version number, never Claude Code acting on its own.
When a schema change is made, add the comment line documenting it (see above),
but leave the returned integer untouched unless the user explicitly asks for the bump itself.

#### Consistency Checks: SQL First

Enforce invariants at the SQL level whenever possible;
fall back to Python only when SQL cannot express the check or cannot repair a violation:

- A **single-row invariant** (what a column may hold given the other columns in the same row)
  is a `CHECK` constraint on the table,
  e.g. the `step` table's `CHECK (NOT postponed OR state = PENDING)`.
- An invariant spanning **multiple rows** (e.g. a node's creator must have a compatible `kind`)
  cannot be a `CHECK` constraint —
  SQLite does not re-evaluate a `CHECK` when a *different* row changes.
  Use a `RAISE(ABORT, ...)` trigger instead (see [Triggers](#triggers) below).
- A **graph-wide** invariant (e.g. every attached node must be reachable from the root via
  creator edges) can require recursing over an unbounded number of rows,
  which a trigger cannot do either — a trigger only sees the row(s) touched by the statement
  that fired it.
  Check these in Python via a recursive SQL query,
  run once at startup after opening an existing database (`Trellis._check_consistency()`),
  not on every mutation:
  since every write already goes through the CHECK/trigger-guarded path,
  a startup-only pass is enough to catch whatever a crash could have left behind.
- A startup check that also **repairs** what it finds
  (e.g. `Workflow._check_consistency()` marking a succeeded step with a non-`BUILT` output
  back to `PENDING`) belongs in Python regardless,
  since a `CHECK`/trigger can only reject a write, not fix one up.
- Remove a Python-side check once a `CHECK` constraint or trigger already covers the same write
  path — it can no longer fire, so keeping it "for safety" just adds dead code
  (e.g. the file-hash-missing check dropped from `Workflow._check_consistency()` once the
  `file` table's own `CHECK (state NOT IN (...) OR hash IS NOT NULL)` made it unreachable).

#### Triggers

Invariant-preserving side effects (derived-column bookkeeping that would otherwise require
a Python read-branch-write round trip on every mutation) are implemented as
`AFTER INSERT/UPDATE/DELETE` triggers, colocated with the table they read from inside that
node class's `*_SCHEMA` string.
Triggers are also used for pure validation (`RAISE(ABORT, ...)`) of multi-row invariants that a
`CHECK` constraint cannot express,
e.g. `node_check_creator_kind_ins`/`_upd` and `dependency_check_kinds_ins` (`WORKFLOW_SCHEMA`,
`workflow.py`), which replaced the Python-side `Workflow._check_creator`/`_check_source` hooks.
Trigger names follow the same `<table>_<purpose>` convention as indexes, with no prefix.
`WHEN` clauses that depend on enum values are generated via f-string interpolation against
the enum (e.g. `{StepState.SUCCEEDED.value}`) rather than hardcoded literals,
so they can never drift from `enums.py`.

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
  a reusable mechanism for output path construction in `make_path_out()` in `stepup.core.path`.
- A local executable must contain at least one slash, e.g., `./script.sh` or `bin/script.sh`.
- The `getenv()` function in `stepup.core.api` preserves path affixes
  when reading environment variables must be treated as paths.
- A static tree path in the database is always stored with a trailing slash.

The `get_affixes()` and `apply_affixes()` functions in `stepup.core.path` are used to
extract and re-apply the affixes when needed.

### User-Facing API (`api.py`)

`plan.py` scripts call functions in `api.py` (e.g., `static()`, `step()`, `glob()`)
which send RPC calls to the director.
The module must not be imported by other `stepup.core` modules,
except `interact.py` (top-level import) and `call.py`, `script.py`, `run.py`,
`render_jinja.py`, and `extapi.py` (local, inside-function imports only).

### Extension Developer API (`extapi.py`)

`extapi.py` collects utilities for authors of StepUp extension packages:
`subs_env_vars`, `record_subprocess`, `run_subprocess`, `filter_dependencies`,
and `get_local_import_paths`.
`get_rpc_client`, `RPC_CLIENT` and `get_job_i` live in `api.py`, not `extapi.py`:
together they are how an extension addresses the director.
`subs_env_vars` is re-exported from `api.py` for backward compatibility.
`extapi.py` imports from `api.py` only via local (inside-function) imports to avoid
circular dependencies at module load time.

### Step Execution Pipeline

1. `Scheduler` (`scheduler.py`) picks the highest-priority runnable step
   from the `Workflow` and creates a corresponding `Job` instance.
2. `Builder` (`builder.py`) requests a runnable job from the scheduler and, up to the
   concurrency limit, starts it as an asyncio task on the shared `Executor`.
3. `Executor` (`executor.py`) runs the step's command (subprocess or forkserver child),
   which may produce more RPC calls back to the director.
4. The executor computes file hashes in a background thread and updates
   `FileState` and `StepState` in the workflow.

### Named Globs (`nglob.py`)

`NGlobSingle` / `NGlobMulti` implement pattern matching with named back-references (`${*name}`).
Used in the API for dynamic file discovery with consistency constraints across patterns.

### Key Environment Variables

| Variable | Purpose |
| --- | --- |
| `STEPUP_DEBUG` | Debug mode: implies `STEPUP_LOG_LEVEL=DEBUG` and makes internal consistency checks fatal instead of self-correcting |
| `STEPUP_BUILD_DURATION` | Measure step durations to optimize scheduling (set `0` to disable in tests) |
| `STEPUP_BUILD_FORKSERVER` | Run Python steps via forkserver (`1`) or plain subprocesses (`0`); CI tests both |
| `STEPUP_SYNC_RPC_TIMEOUT` | Timeout for sync RPC calls (seconds) |
| `STEPUP_BUILD_PERF` | Frequency (Hz) for Linux `perf` profiling of director |
| `STEPUP_BUILD_YAPPI` | Enable Yappi profiling of director |
| `STEPUP_BUILD_SQLLOG` | Log all SQLite queries with timings |
| `STEPUP_BUILD_JOBLOG` | Log per-job scheduling and timing information |

Profiling output (`perf`, sqllog, joblog) can be analyzed with `tools/analyze_perf.py`.

Several other `STEPUP_*` / `STEPUP_BUILD_*` variables exist for configuration
(e.g. `STEPUP_BUILD_JOBS`, `STEPUP_BUILD_RESOURCES`, `STEPUP_BUILD_KEEP_GOING`,
`STEPUP_LOG_LEVEL`, `STEPUP_MAX_OUTPUT_SIZE`, `STEPUP_PATH_FILTER`)
— see `docs/reference/configuration.md` for the full list.
Most StepUp-3-era `STEPUP_*` variables gained a `STEPUP_BUILD_` prefix in the 4.0 migration;
see `docs/migration/from_3x_to_40.md` before assuming an old name still applies.

### Test Structure

- `tests/conftest.py` defines fixtures:
    - `wfs` (bare workflow)
    - `wfp` (workflow with plan.py),
    - `client` (full director running in-process),
    - `path_tmp` (pytest's `tmpdir` as a `path.Path`).
- `tests/examples/*/` contains integration test cases,
  each with `plan.py`, `main.sh`, and `expected_stdout*.txt` / `expected_graph*.txt`.
  These are run by `tests/test_examples.py`.
  See `tests/examples/README.md` for a detailed explanation of the `main.sh` conventions
  and how the test builder compares `current_*` files against `expected_*` files.
    - Register each new example in the `EXAMPLES` list at the top of `tests/test_examples.py`
      (and in the `test_plan` parametrize list if the plan should also run standalone).
      The guard tests `test_examples_list_has_all_dirs` / `test_examples_list_has_no_extra`
      fail when `EXAMPLES` is out of sync with the directories under `tests/examples/`.
    - Examples that only work with the forkserver must be added to
      `EXAMPLES_REQUIRES_FORKSERVER`; they are skipped when `STEPUP_BUILD_FORKSERVER=0`.
    - CI runs the example suite twice, with `STEPUP_BUILD_FORKSERVER=1` and `=0`, so examples
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

Never invoke two `pytest` runs concurrently
(e.g. one in the background while another runs in the foreground),
such as separately running the example suite
with `STEPUP_BUILD_FORKSERVER=0` and `=1`.
Each invocation already parallelizes internally via `pytest-xdist`
(`-n auto --dist worksteal`);
running a second invocation at the same time doubles up worker processes and overloads the
system, which is a real cause of flaky failures in timing-sensitive examples
(e.g. `hold_orders_by_duration`, which relies on real `time.sleep()` calls to prove
non-dispatch).
Run `pytest` invocations one after another instead.

### Release Process

1. Update `docs/changelog.md`.
2. Commit and tag: `git tag vX.Y.Z`.
3. Push with tags: `git push origin main --tags` — triggers PyPI GitHub Action.
