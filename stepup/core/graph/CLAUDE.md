<!--
SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
SPDX-License-Identifier: LGPL-3.0-or-later
-->

# The Workflow Graph

This file documents `stepup/core/graph/`.
The layering of the subpackages and the process model are in `stepup/core/CLAUDE.md`.

## Workflow Graph (`trellis.py`, `workflow.py`)

The core data structure is a combined **provenance** and **dependency** graph stored in SQLite.
`Trellis` (in `trellis.py`) is the abstract base implementing the graph, leveraging recursive SQL.
`Workflow` (in `workflow.py`) extends it with concrete node types:
`File` (`file.py`), `Step` (`step.py`),
and `StaticTree` (`static_tree.py`, used for inputs that are automatically declared as static).
Their states are defined in `stepup/core/enums.py`.

All graph mutations happen inside SQLite transactions.
The `DBSession` in `sqlite3.py` serializes writes.

A file node also has a **role** (`FileRole` in `enums.py`): who declares the file,
as opposed to where it currently sits in its lifecycle.
`FILE_STATES_BY_ROLE` and its inverse `FILE_ROLE_BY_STATE` are the only place where the
partitioning of `FileState` into roles is written down;
derive any "all states of this role" test from them instead of spelling out a state tuple.
Not every state set is a role, though:
"available as an input" (`BUILT`, `CONFIRMED`) cuts across two roles and includes neither fully,
and several other partial sets are genuinely about a lifecycle phase.
The rules for when a role determines a state, and why `UNDECLARED` is not simply
"the roleless state", are in the `FileRole` and `FileState` docstrings.

### Graph Determinism

**The graph must be a deterministic function of the source files and the plan/step code,
never of scheduling details: dispatch order, job count, resource limits or step durations.**
Running the same project with `-j1` and `-j16` must produce the same graph,
and so must two runs that change nothing,
whether the second starts from scratch or resumes a valid workflow database.
This constrains the *provenance* half of the graph as much as the dependency half:
which node is recorded as the creator of a file may not depend on
which of two concurrent steps happened to declare it first.

Note the requirement is deliberately stated relative to the source files and the code,
not in absolute terms:
`amend()` lets a step extend the graph based on what it discovers at run time,
so a step whose behaviour genuinely varies (on wall-clock time, on undeclared
environment state) will legitimately produce a different graph.
What must never vary is the graph produced by the *same* inputs and code.
The declared way for a step to vary between builds is to track `STEPUP_STARTUP_I`,
spelled `recheck=True` in the API,
which the director sets to a new integer at every start (`Workflow.assign_startup_i`),
except with `--offline`, which keeps the value of the previous start.
Such a step runs at every start and may read what StepUp does not track, such as a remote,
so the graph that it extends is a function of that remote state as well.

The determinism goal applies to the graph of a **successful** build.
It does not extend to *how* an invalid workflow is reported:
when two steps' declarations conflict, whichever runs first may be the one that raises,
so which step fails, and where its traceback points, can depend on execution order.
What may not vary is whether the build succeeds or fails,
and, on success, the graph that results.
The *text* of such an error is nevertheless made order-independent whenever the raise site
knows both parties, since the plan is equally wrong in either order.

Why this matters:

- **Testability.**
  A deterministic graph is one that `expected_graph*.txt` files can pin down.
  A graph that depends on dispatch order can only be asserted loosely, or not at all,
  and it makes timing-sensitive tests flaky for reasons unrelated to what they test.
- **Debugging.**
  The graph is what a user inspects (`stepup graph`, `stepup browse`) when a build
  misbehaves. If it varies between runs, a reported graph no longer describes the run
  that produced it, and bug reports stop being reproducible.
- **Reproducibility.**
  The graph survives restarts and drives watch mode, so it is a durable artifact,
  not an internal detail. For a tool whose value proposition is persistent provenance,
  determinism of the graph *is* determinism of the outcome.
- **Avoiding spurious re-execution.**
  When a node changes creator, `Trellis.create` calls `after_lost_product()` on the old creator,
  which for a `Step` deletes its stored hash (`Step.after_lost_product`), so that step can no
  longer be skipped. A creator assignment that varies between runs therefore causes steps
  to re-run even though their inputs and outputs are unchanged.

The practical consequence for API design:
an operation that merely *observes* files (such as pattern matching) must not
acquire ownership of graph nodes, since ownership is what makes call order observable.
`StaticTree` is the model to follow: it owns the files under it regardless of
which step first referenced them, so its identity is order-independent by construction.

### Database Schema Versioning

The schema version is `Trellis.schema_version` (in `trellis.py`), written to the database via
`PRAGMA user_version`. On a version mismatch, the database is **wiped and recreated** from
scratch (`DBSession._wipe_database`), because there is no `ALTER TABLE` migration path.

Note that `DBSession.apply_schema()` re-executes the full schema (`CREATE TABLE IF NOT EXISTS`,
`CREATE INDEX IF NOT EXISTS`, ...) via `executescript` on **every** database open, regardless of
whether `user_version` matched. A purely additive change (e.g. a new index) is therefore applied
lazily even to a database whose `schema_version` didn't change.
Bumping the version is a documentation/consistency convention for this project,
not strictly required for such a change to take effect.

**Policy: bump `schema_version` at most once per release.**
During a pre-release refactor, many commits may change the schema,
but they all share the single bumped version for the upcoming release;
do not bump the version again within the same release cycle.

Who may bump the version is a non-negotiable in the top-level `CLAUDE.md`.

### Consistency Checks: SQL First

Enforce invariants at the SQL level whenever possible;
fall back to Python only when SQL cannot express the check or cannot repair a violation:

- A **single-row invariant** (what a column may hold given the other columns in the same row)
  is a `CHECK` constraint on the table,
  e.g. the `step` table's `CHECK (NOT deferred OR state = PENDING)`.
- An invariant spanning **multiple rows** (e.g. a node's creator must have a compatible `kind`)
  cannot be a `CHECK` constraint,
  because SQLite does not re-evaluate a `CHECK` when a *different* row changes.
  Use a `RAISE(ABORT, ...)` trigger instead (see [Triggers](#triggers) below).
- A **graph-wide** invariant (e.g. every attached node must be reachable from the root via
  creator edges) can require recursing over an unbounded number of rows,
  which a trigger cannot do either,
  since a trigger only sees the row(s) touched by the statement that fired it.
  Check these in Python via a recursive SQL query,
  run once at startup after opening an existing database (`Trellis._check_consistency()`),
  not on every mutation:
  since every write already goes through the CHECK/trigger-guarded path,
  a startup-only pass is enough to catch whatever a crash could have left behind.
  These checks raise `ConsistencyError`, not `GraphError`:
  no plan can reach them through the public API, so they report a bug in StepUp,
  and unlike a `GraphError` (a `UsageError`) they keep their full traceback on the client.
- A startup check that also **repairs** what it finds
  (e.g. `Workflow._check_consistency()` marking a succeeded step with a non-`BUILT` output
  back to `PENDING`) belongs in Python regardless,
  since a `CHECK`/trigger can only reject a write, not fix one up.
- Remove a Python-side check once a `CHECK` constraint or trigger already covers the same write
  path, because it can no longer fire, so keeping it "for safety" just adds dead code.

### Triggers

Invariant-preserving side effects (derived-column bookkeeping that would otherwise require
a Python read-branch-write round trip on every mutation) are implemented as
`AFTER INSERT/UPDATE/DELETE` triggers, colocated with the table they read from inside that
node class's `*_SCHEMA` string.
Triggers are also used for pure validation (`RAISE(ABORT, ...)`) of multi-row invariants that a
`CHECK` constraint cannot express,
e.g. `node_check_creator_kind_ins`/`_upd` and `dependency_check_kinds_ins`
(`WORKFLOW_SCHEMA`, `workflow.py`).
Which table such a trigger is placed on is part of the invariant, not a free choice:
`file_check_undeclared_detached_ins`/`_upd` (`FILE_SCHEMA`, `file.py`) guards
"`state = UNDECLARED` implies the node is detached" from the `file` side only,
because the equivalent trigger on `node` would abort on the legitimate window in which
`Trellis.create` re-attaches a recycled node before its new state is written.
Trigger names follow the same `<table>_<purpose>` convention as indexes, with no prefix.
`WHEN` clauses that depend on enum values are generated via f-string interpolation against
the enum (e.g. `{StepState.SUCCEEDED.value}`) rather than hardcoded literals,
so they can never drift from `enums.py`.
