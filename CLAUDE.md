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

Guidance that applies to only part of the repo lives next to the code it governs:

- `stepup/core/CLAUDE.md` — process model, workflow-graph invariants, database conventions.
- `tests/CLAUDE.md` — test layout and the integration-example suite.
- `docs/CLAUDE.md` — regenerating the tutorial output.
- `.claude/skills/release/SKILL.md` — the release procedure.

## Non-Negotiables

- **Claude Code must never bump `Trellis.schema_version`.**
  Deciding when a release's schema changes are complete is a human judgment call.
- **The workflow graph must be a deterministic function of the source files and the
  plan/step code, never of scheduling order or job count.**
  Running the same project with `-j1` and `-j16` must produce the same graph.
  Rationale and consequences in `stepup/core/CLAUDE.md`.

## Commands

### Environment

The development environment is managed with [uv](https://docs.astral.sh/uv/), not with `pip`.
Reinstall the development version with:

```bash
uv sync --extra dev
```

This is needed after changing the `stepup.tools` entry points in `pyproject.toml`,
because subcommands are looked up through the installed package metadata,
so a renamed or added subcommand is invisible until the environment is synced.

### Linting

Pre-commit hooks run `ruff format` and `ruff check` automatically on commit.
After making code changes, run all pre-commit checks before considering the work done:

```bash
pre-commit run --all
```

### Tests

The following test command completes quickly as it skips the integration tests:

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

## Coding Conventions

### Semantic Line Breaks

All English text in this repo — comments (including SQL comments), docstrings,
Markdown documentation, commit messages, etc. — is wrapped using **semantic line breaks**:
break after sentences or logical units, not at a fixed character count.
See <https://sembr.org/>.
Sentences always start on a new line,
but multiple clauses within one sentence can be on the same line if they fit.
This makes diffs to prose easier to review,
since editing one sentence doesn't reflow unrelated lines.
The 100-character line length (see Linting below) is a hard cap, not a target to fill.

### Avoid En and Em dashes

Write sentences without en or em dashes.
They should never be used in any prose (code comments, docstrings, Markdown, ...),
neither in their UTF-8 glyph form (–, —) nor in ASCII form (--, ---).
Subclauses should be made explicit (e.g. "which", "because", "that")
or split into separate sentences.

### Prose That Ages Well

Stale prose is worse than no prose. When writing comments, docstrings, or other prose, avoid:

- **Describing callers.** Don't note how other code uses a function or class.
  That's the caller's concern, and the remark silently rots when the caller changes.
- **Describing history.** Don't explain what the code used to do or how it changed.
  The current code should speak for itself; history belongs in commit messages.
- **Implementation details in docstrings.** Document the contract (how to use something),
  not how it works internally.
- **Line-number references.** They break as soon as the file changes.
  Point to a function, class, or file name instead.
- **Restating the code.** A comment should say something the code doesn't already say
  (the reason, the invariant, the non-obvious constraint) not paraphrase the next line.
  A purely redundant comment isn't wrong, so nothing forces it to be updated,
  and it drifts out of sync silently.
- **Repetitive and duplicate comments.**
  If a remark is repeated in multiple places, it will rot in one place when updated in another.
  Factor out the common remark into a single function or class docstring, or a Markdown file in `docs/`.
- **Timeless phrasing for point-in-time claims.** An empirical observation about an external
  tool or environment (e.g. "SQLite's planner never picks this index") can stop being true
  after a version upgrade, with nothing to flag the comment as outdated.
  Say what was observed and, when it matters, on what
  (e.g. "as of SQLite 3.45, measured separately").

### Linting (ruff)

Do not add `# noqa` comments unless the violation is a genuine false positive that cannot
be resolved by restructuring the code — the project's rule set already excludes rules
that would fire spuriously in this codebase.

### Docstrings

Use **NumPy-style** sections (`Parameters`, `Returns`, `Raises`, ...)
Some conventions specific to this codebase:

- Docstrings are written in Markdown, not reStructuredText! Some important gotcha's:
    - Do not use italics for parameter names, return values, or exception names.
      Use single backticks instead.
    - Use single backticks for all inline code, not double backticks.
    - Use triple backticks for code blocks,
      and specify the language for syntax highlighting (e.g., ```python).
- Lines are wrapped using semantic breaks, per [Semantic Line Breaks](#semantic-line-breaks) above.
- Use the imperative mood for function descriptions
  (e.g., "Compute the hash of a file."),
  except for `@property` getters where the description should be a noun phrase
  (e.g., "The parent directory path.").
- Do not repeat type annotations in the docstring — they are already in the function signature.
- In `Parameters` sections, use the **parameter name** as the heading for each parameter.
  Grouping closely related parameters under a combined heading
  (e.g., `stdout, stderr`) is allowed when parameters are better described together.

- In `Returns` sections, use a **semantic name** for the return value, not the type,
  as these are already in the function signature.

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

### Data Classes

The project uses `attrs` for data classes,
in practice for as many classes as possible:
`@attrs.define` (with `frozen=True` for value objects, and `order=True` when instances are
sorted), fields declared with `attrs.field()`, and a docstring under each field.
Prefer this over `dataclasses`, `typing.NamedTuple` or a hand-written `__init__`.

### Dependencies

Runtime dependencies are declared in `pyproject.toml` under `[project] dependencies`.
Before adding a lazy import or a try/except ImportError guard, check whether the package
is already a declared dependency and import it at the top of the file instead.

## Configuration

`STEPUP_DEBUG` is worth knowing about:
it makes the default log level of `stepup build` `DEBUG` and makes internal consistency checks
fatal instead of self-correcting.
Profiling output (`STEPUP_BUILD_PERF`, `STEPUP_BUILD_SQLLOG`, `STEPUP_BUILD_JOBLOG`)
can be analyzed with `tools/analyze_perf.py`.

See `docs/reference/configuration.md` for the full list of `STEPUP_*` / `STEPUP_BUILD_*`
variables.
Most StepUp-3-era `STEPUP_*` variables gained a `STEPUP_BUILD_` prefix in the 4.0 migration;
see `docs/migration/from_3x_to_40.md` before assuming an old name still applies.
