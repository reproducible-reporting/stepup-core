# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""A `Step` is a command that can be executed and that has inputs and/or outputs."""

import json
import logging
import os
from collections.abc import Collection, Iterator
from typing import Literal

import attrs
from path import Path

from .cattrs import json_converter
from .enums import FILE_STATES_BY_ROLE, FileRole, FileState, Need, StepState
from .exceptions import GraphError
from .file import File
from .hash import FileHash, StepHash
from .nglob import NamedGlob, convert_nglob_to_regex
from .outcome import ChildOutcome, ResourceUsage
from .static_tree import StaticTree
from .stepinfo import StepInfo
from .trellis import Node, NodeType
from .utils import format_digest

__all__ = (
    "RESERVED_ENV_VARS",
    "STEP_DISPATCH_WHERE",
    "UNAVAILABLE_INPUT_WHERE",
    "PathRecord",
    "Step",
    "truncate_output",
    "unavailable_input_sql",
)


logger = logging.getLogger(__name__)


# Environment variables that StepUp sets for each step (see Executor._run_command).
# These are managed by StepUp and can never be declared as (initial/dynamic) dependencies by a step.
RESERVED_ENV_VARS = frozenset(
    {"HERE", "ROOT", "STEPUP_JOB_I", "STEPUP_STEP_INP_DIGEST", "STEPUP_STEP_NEED"}
)


# Step-only predicates for the step_dispatch partial index (see STEP_SCHEMA below) and for
# scheduler.SELECT_NEXT_STEP's WHERE clause, which must stay textually identical to this
# fragment, because SQLite matches partial-index eligibility structurally against the query text.
# node.detached and resource availability are deliberately excluded: both live outside the
# step table (node/step_resource respectively), so a partial index on step cannot express
# them; SELECT_NEXT_STEP re-checks them lazily per examined index row instead.
#
# The second disjunct lets a hash-checkable step bypass an active hold() on one of its
# (recursive) creators: verifying a stored hash is cheap and "unlocks more work early" (see
# SELECT_NEXT_STEP's `_has_hash DESC` ORDER BY term), and hold() exists to control the
# dispatch *order* of real reruns, not to delay cheap checks. A hash mismatch still produces
# a RunJob (Executor.try_skip_job -> _reset_step_to_pending -> delete_hash), which drops
# _has_hash back to 0 and is therefore gated normally by _safe (including hold) from then on.
STEP_DISPATCH_WHERE = f"""step.state = {StepState.PENDING.value} AND
    (step._safe OR (step._has_hash AND step._safe_ignoring_hold)) AND
    NOT step.deferred AND
    step._implied_need > {Need.OPTIONAL.value} AND
    step._ready"""


# Boolean expression identifying an input file that blocks a step from running.
# It references three aliases the enclosing query must provide:
# `input_file` (file row), `input_node` (node row) and `dynamic_dep`
# (LEFT JOINed dynamic_dep row, NULL for an initial dependency).
# Shared verbatim by dispatch (RECOMPUTE_READY in scheduler.py, which builds its subquery
# with `unavailable_input_sql` below) and by the end-of-build analysis in pending.py,
# so the two can never disagree about what "blocked" means.
UNAVAILABLE_INPUT_WHERE = f"""
input_file.state = {FileState.VOLATILE.value} OR
(
    -- Case 1: Is a dynamic dependency
    dynamic_dep.i IS NOT NULL AND
    NOT input_node.detached AND
    input_file.state IN ({FileState.PLANNED.value}, {FileState.OUTDATED.value})
) OR
(
    -- Case 2: Is an initial dependency
    dynamic_dep.i IS NULL AND
    (
        input_node.detached OR
        input_file.state NOT IN ({FileState.BUILT.value}, {FileState.CONFIRMED.value})
    )
)
"""


def unavailable_input_sql(node_expr: str) -> str:
    """Return an `EXISTS`-ready subquery for the unavailable inputs of one step.

    Only ever used inside `EXISTS(...)`/`NOT EXISTS(...)`,
    so the projected column is irrelevant to the result.
    `SELECT 1` avoids depending on an outer `node` alias that may not be in scope
    (e.g. `RECOMPUTE_READY`'s bare `UPDATE step ...`).

    Parameters
    ----------
    node_expr
        The SQL expression identifying "this step's node id" in the enclosing query:
        `node.i` when joined against `node`/`step`,
        or `step.node` when there is no `node` table in scope.

    Returns
    -------
    subquery
        A `SELECT` statement, suitable as the body of an `EXISTS`/`NOT EXISTS` clause.
    """
    return f"""
    SELECT 1
    FROM dependency AS dep
    JOIN file AS input_file ON input_file.node = dep.source
    JOIN node AS input_node ON input_node.i = dep.source
    LEFT JOIN dynamic_dep ON dynamic_dep.i = dep.i
    WHERE dep.sink = {node_expr} AND ({UNAVAILABLE_INPUT_WHERE})
    """


STEP_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS step (
    -- Main data
    node INTEGER PRIMARY KEY,
    -- The node of the step in the node table.
    state INTEGER NOT NULL CHECK(state >= {min(StepState)} AND state <= {max(StepState)}),
    -- The state of the step, as defined in the StepState enum.
    need INTEGER NOT NULL CHECK(
        need IN ({Need.OPTIONAL.value}, {Need.DEFAULT.value}, {Need.PLAN.value})
    ),
    -- The need of the step, as defined in the Need enum.
    -- TARGET is deliberately excluded: target steps are derived, never stored in the need column.
    -- The result lives exclusively in _implied_need and is never persisted here.
    duration REAL NOT NULL CHECK(duration >= 0) DEFAULT 1.0,
    -- An estimate of the wall time of the step in seconds.
    deferred INTEGER NOT NULL CHECK(deferred IN (0, 1)) DEFAULT 0,
    -- Whether the step is deferred due to missing inputs (see StepState.PENDING).
    defer_count INTEGER NOT NULL CHECK(defer_count >= 0) DEFAULT 0,
    -- Number of consecutive defers since the last SUCCEEDED state.
    -- Reset to 0 only on SUCCEEDED (see step_reset_defer_count below);
    -- NOT reset by FAILED or by deferred being cleared via mark_pending().
    shell INTEGER NOT NULL CHECK(shell IN (0, 1)),
    -- Whether the step command is executed via a shell (shell=True).
    env_overrides TEXT,
    -- JSON-encoded dict[str, str] of step-specific environment variable overrides, or NULL.
    -- Applied to the child process environment when the step runs.
    -- Metadata
    _safe INTEGER NOT NULL CHECK(_safe IN (0, 1)),
    -- Whether this step is safe to run, meaning that all its (recursive) creators
    -- are in a state that allows queuing this step (RUNNING or SUCCEEDED).
    _check_safe INTEGER NOT NULL CHECK(_check_safe IN (0, 1)),
    -- Whether recent changes to this step imply updates of the _safe metadata field of others.
    _holding INTEGER NOT NULL CHECK(_holding >= 0) DEFAULT 0,
    -- Number of open (unmatched) `hold()` calls on this step, i.e. how many `release()`
    -- calls are still owed. Nonzero means this step is holding back its descendant steps
    -- from dispatch. Consulted by SELECT_SAFE_UPDATE alongside creator.state
    -- when computing a descendant's _safe.
    _safe_ignoring_hold INTEGER NOT NULL CHECK(_safe_ignoring_hold IN (0, 1)) DEFAULT 0,
    -- Like _safe, but computed as if no step anywhere in the (recursive) creator chain were
    -- holding, i.e. the same ancestor RUNNING/SUCCEEDED walk without ever consulting
    -- _holding. Computed alongside _safe by SELECT_SAFE_UPDATE. Used by STEP_DISPATCH_WHERE
    -- to let hash-checkable (_has_hash) steps bypass an active hold: a step that is only
    -- unsafe because of a hold (_safe_ignoring_hold true, _safe false) may still be verified
    -- promptly, since checking is cheap and a hash mismatch falls back to the ordinary
    -- hold-gated RunJob path (see STEP_DISPATCH_WHERE's comment).
    _implied_need INTEGER NOT NULL CHECK(
        _implied_need >= {min(Need)} AND _implied_need <= {max(Need)}
    ),
    -- The need that is implied by sinks, as defined in the Need enum.
    _tail_time REAL NOT NULL CHECK(_tail_time >= 0) DEFAULT 1.0,
    -- The tail_time of this step, defined as the total duration of the critical path
    -- from this step to the exit nodes of the workflow.
    _check_after INTEGER NOT NULL CHECK(_check_after IN (0, 1)),
    -- Whether recent changes to this step require the recalculation of the _implied_need
    -- metadata of this step and its sources.
    _has_hash INTEGER NOT NULL CHECK(_has_hash IN (0, 1)) DEFAULT 0,
    -- Whether a step_hash row exists for this step. Trigger-maintained mirror of
    -- EXISTS(SELECT 1 FROM step_hash WHERE node = step.node), used by the step_dispatch
    -- index so SELECT_NEXT_STEP does not need a correlated EXISTS per candidate row.
    _ready INTEGER NOT NULL CHECK(_ready IN (0, 1)) DEFAULT 0,
    -- Cached negation of the "unavailable input" test: whether all inputs of this step are
    -- currently available. Recomputed for _check_ready-flagged steps by
    -- Scheduler._update_meta_ready(). The conservative default 0 (paired with
    -- _check_ready = 1 below) means a new or recycled step is never dispatched before its
    -- readiness has been computed at least once.
    _check_ready INTEGER NOT NULL CHECK(_check_ready IN (0, 1)) DEFAULT 1,
    -- Whether _ready must be recomputed because something relevant to it changed.

    FOREIGN KEY (node) REFERENCES node(i) ON DELETE CASCADE,
    -- "Ignoring hold" can never be a *stricter* condition than "respecting hold": every hold is
    -- a restriction, never a relaxation, of dispatchability. This is a structural guarantee that
    -- SELECT_SAFE_UPDATE's safe/safe_nh sub-expressions (stepup/core/scheduler.py) never
    -- silently diverge in the wrong direction, e.g. if a future third hold-bypass condition is
    -- added and one of the (then six-plus) sub-expressions is miscopied.
    CHECK (_safe_ignoring_hold >= _safe),
    -- A step can only be deferred while it is PENDING (see StepState.PENDING). This is the
    -- sole enforcement of that rule: Step.set_state() must not duplicate it in Python.
    -- Every write path that changes state also writes deferred in the same statement
    -- (Step.set_state defaults it to False; the trigger-driven clears below only ever move
    -- it to False), so this never goes transiently false mid-statement.
    CHECK (NOT deferred OR state = {StepState.PENDING.value})
) WITHOUT ROWID;

-- Indexes for efficient querying
CREATE INDEX IF NOT EXISTS step_state ON step(state);
CREATE INDEX IF NOT EXISTS step_implied_need ON step(_implied_need);
-- Partial indexes over the scheduler's "to recompute" flags. They contain only the few
-- flagged rows, so locating them each scheduling tick (see Scheduler._update_meta_*) is an
-- index scan proportional to the flagged count instead of a full scan of the step table.
CREATE INDEX IF NOT EXISTS step_check_safe ON step(node) WHERE _check_safe;
CREATE INDEX IF NOT EXISTS step_check_after ON step(node) WHERE _check_after;
CREATE INDEX IF NOT EXISTS step_check_ready ON step(node) WHERE _check_ready;
-- The dispatch index for SELECT_NEXT_STEP. Key order matches that query's ORDER BY
-- exactly, and the WHERE clause matches STEP_DISPATCH_WHERE above exactly, so the query
-- can walk this index in priority order and stop at the first eligible row (LIMIT 1)
-- without materializing or sorting the candidate set. SQLite's planner does not pick this
-- index voluntarily (even after ANALYZE), so the query pins it with INDEXED BY.
CREATE INDEX IF NOT EXISTS step_dispatch ON step(
    _has_hash DESC,
    (_implied_need = {Need.PLAN.value}) DESC,
    (_tail_time / (1 + defer_count)) DESC
) WHERE {STEP_DISPATCH_WHERE};

-- Convention for this trigger block: single-row, same-table consequences of a column
-- write live here as triggers, colocated with the table whose column they maintain
-- regardless of which table's event fires them (e.g. the dependency/file/node/step_hash
-- triggers below all live here because they maintain step._check_after/_check_ready).
-- Multi-row / recursive graph consequences (e.g. flagging a step's recursive products, or
-- steps reached across dependency edges two hops away) stay in explicit Python-invoked SQL
-- instead: see RECURSIVE_CHECK_WITH_PRODUCTS and RECURSIVE_CHECK_AFTER_SOURCES below (used
-- by Step.detach()/Step.reattach()), which together with the triggers here account for the
-- complete _check_safe/_check_after/_check_ready bookkeeping story.

-- Keep _check_after/_check_ready in sync with dependency-edge changes touching either
-- endpoint. Only the sink side is relevant to _check_ready (a step's inputs are the
-- dependencies where it is the sink). A no-op UPDATE (zero rows matched) is harmless when
-- the other endpoint is not a step.
CREATE TRIGGER IF NOT EXISTS step_dependency_check_after_ins AFTER INSERT ON dependency
BEGIN
    UPDATE step SET _check_after = 1 WHERE node IN (NEW.source, NEW.sink);
    UPDATE step SET _check_ready = 1 WHERE node = NEW.sink;
END;
CREATE TRIGGER IF NOT EXISTS step_dependency_check_after_del AFTER DELETE ON dependency
BEGIN
    UPDATE step SET _check_after = 1 WHERE node IN (OLD.source, OLD.sink);
    UPDATE step SET _check_ready = 1 WHERE node = OLD.sink;
END;

-- Keep _check_ready in sync with file state changes, so the scheduler recomputes
-- readiness for every step that consumes this file as an input.
-- File.set_state issues a plain UPDATE (fires the _upd trigger);
-- File.initialize_row's upsert fires the _upd trigger on its conflict/update arm
-- and the _ins trigger on its fresh-insert arm.
-- The _ins arm matters for recycled file nodes that may already have dependency edges.
CREATE TRIGGER IF NOT EXISTS step_file_check_ready_upd AFTER UPDATE OF state ON file
WHEN OLD.state != NEW.state
BEGIN
    UPDATE step SET _check_ready = 1
    WHERE node IN (SELECT sink FROM dependency WHERE source = NEW.node);
END;
CREATE TRIGGER IF NOT EXISTS step_file_check_ready_ins AFTER INSERT ON file
BEGIN
    UPDATE step SET _check_ready = 1
    WHERE node IN (SELECT sink FROM dependency WHERE source = NEW.node);
END;

-- Keep _check_ready in sync with node.detached flips (set by RECURSIVELY_SET_DETACHED in
-- trellis.py, a bulk UPDATE that still fires this row trigger once per affected row). Only
-- *consumers* of the flipped node need flagging: a step's own node.detached is checked
-- lazily in SELECT_NEXT_STEP itself, the same treatment as the resource check.
CREATE TRIGGER IF NOT EXISTS step_node_check_ready_detached AFTER UPDATE OF detached ON node
WHEN OLD.detached != NEW.detached
BEGIN
    UPDATE step SET _check_ready = 1
    WHERE node IN (SELECT sink FROM dependency WHERE source = NEW.i);
END;

-- Keep _check_after in sync with duration changes, so the scheduler recomputes
-- _implied_need/_tail_time for this step (and, via propagation, its sources).
CREATE TRIGGER IF NOT EXISTS step_flag_check_after_duration AFTER UPDATE OF duration ON step
BEGIN
    UPDATE step SET _check_after = 1 WHERE node = NEW.node;
END;

-- Keep _check_safe in sync with state changes, so the scheduler recomputes the _safe
-- metadata of this step (and, via propagation, its products).
CREATE TRIGGER IF NOT EXISTS step_flag_check_safe AFTER UPDATE OF state ON step
BEGIN
    UPDATE step SET _check_safe = 1 WHERE node = NEW.node;
END;

-- _holding only ever grows/shrinks while this step's own execution is live and RUNNING:
-- hold()/release() (DirectorHandler.hold()/DirectorHandler.release() in director.py)
-- resolve their job_i through Scheduler.get_step(),
-- which only has an entry while a job is in flight.
-- So a step leaving RUNNING for any reason means the execution that owned the
-- counter is gone, and any leftover count must not survive into the step's next attempt.
-- The RUNNING state can be left for several reasons: normal completion, a hash-check rerun,
-- mark_step_pending(), or a crash-recovery reset (startup.py resets RUNNING -> FAILED via a
-- raw UPDATE, not through Step.set_state(), which is exactly why this lives in a trigger
-- instead of being reset at each such call site).
-- Firing on every UPDATE OF state (rather than only the paths above) also makes this hold
-- for call sites added later without anyone needing to remember _holding exists.
CREATE TRIGGER IF NOT EXISTS step_reset_holding AFTER UPDATE OF state ON step
WHEN NEW.state != {StepState.RUNNING.value} AND NEW._holding != 0
BEGIN
    UPDATE step SET _holding = 0 WHERE node = NEW.node;
END;

-- Clear deferred once a step reaches a completed state,
-- so a stale defer note from a previous run does not keep gating schedulability
-- after it settles again.
CREATE TRIGGER IF NOT EXISTS step_clear_deferred AFTER UPDATE OF state ON step
WHEN NEW.state IN ({StepState.SUCCEEDED.value}, {StepState.FAILED.value})
BEGIN
    UPDATE step SET deferred = FALSE WHERE node = NEW.node;
END;

-- Reset defer_count only on SUCCEEDED (not FAILED),
-- so the cap measures consecutive defer attempts since the last convergence,
-- independent of the broader clearing of deferred above, which also fires on FAILED.
CREATE TRIGGER IF NOT EXISTS step_reset_defer_count AFTER UPDATE OF state ON step
WHEN NEW.state = {StepState.SUCCEEDED.value}
BEGIN
    UPDATE step SET defer_count = 0 WHERE node = NEW.node;
END;

-- Bucketed counts of non-detached steps by (_implied_need, succeeded), maintained
-- incrementally so Workflow.count_required_steps() can answer with a lookup over at most
-- 2 * (1 + max(Need) - min(Need)) rows instead of scanning every step in the workflow.
-- The bucketing (rather than one row per StepState) is deliberate:
-- count_required_steps() only ever needs "succeeded" vs. "not succeeded",
-- so PENDING/RUNNING/CHECKING/FAILED transitions
-- among each other never have to touch this table at all.
-- Deliberately a temp table (like path_list/node_list in WORKFLOW_SCHEMA), not persisted: that
-- sidesteps ever having to migrate/backfill it for on-disk databases written before this
-- table existed. It starts empty on every fresh connection, so
-- Workflow._rebuild_temp_tables() seeds it once from the steps that already exist before
-- trusting the triggers below to keep it in sync from then on.
CREATE TEMP TABLE IF NOT EXISTS step_need_count (
    implied_need INTEGER NOT NULL,
    succeeded INTEGER NOT NULL CHECK(succeeded IN (0, 1)),
    n INTEGER NOT NULL CHECK(n >= 0),
    PRIMARY KEY (implied_need, succeeded)
) WITHOUT ROWID;

-- Step.initialize_row() always deletes any existing row for this node before inserting a fresh
-- one (never INSERT OR REPLACE), precisely so that reusing a recycled node's step row goes
-- through the ordinary insert/delete triggers below instead of REPLACE's silent,
-- non-trigger-firing implicit delete.
CREATE TEMP TRIGGER IF NOT EXISTS step_need_count_ins AFTER INSERT ON step
WHEN NOT (SELECT detached FROM node WHERE node.i = NEW.node)
BEGIN
    INSERT INTO step_need_count(implied_need, succeeded, n)
    VALUES (NEW._implied_need, NEW.state = {StepState.SUCCEEDED.value}, 1)
    ON CONFLICT(implied_need, succeeded) DO UPDATE SET n = n + 1;
END;
CREATE TEMP TRIGGER IF NOT EXISTS step_need_count_del AFTER DELETE ON step
WHEN NOT (SELECT detached FROM node WHERE node.i = OLD.node)
BEGIN
    UPDATE step_need_count SET n = n - 1
    WHERE implied_need = OLD._implied_need
        AND succeeded = (OLD.state = {StepState.SUCCEEDED.value});
END;

-- Move a step between buckets when its state or _implied_need actually changes the
-- (implied_need, succeeded) key. Most state transitions (e.g. PENDING -> RUNNING) do not,
-- since only the SUCCEEDED / not-SUCCEEDED split matters here.
CREATE TEMP TRIGGER IF NOT EXISTS step_need_count_upd AFTER UPDATE OF state, _implied_need ON step
WHEN NOT (SELECT detached FROM node WHERE node.i = NEW.node)
    AND ((OLD.state = {StepState.SUCCEEDED.value}) != (NEW.state = {StepState.SUCCEEDED.value})
        OR OLD._implied_need != NEW._implied_need)
BEGIN
    UPDATE step_need_count SET n = n - 1
    WHERE implied_need = OLD._implied_need
        AND succeeded = (OLD.state = {StepState.SUCCEEDED.value});
    INSERT INTO step_need_count(implied_need, succeeded, n)
    VALUES (NEW._implied_need, NEW.state = {StepState.SUCCEEDED.value}, 1)
    ON CONFLICT(implied_need, succeeded) DO UPDATE SET n = n + 1;
END;

-- Move a step's counted-ness when its node's detached flag flips (RECURSIVELY_SET_DETACHED
-- in trellis.py is a bulk UPDATE that still fires this row trigger once per affected row, the
-- same treatment as step_node_check_ready_detached above). A no-op when this node has no
-- step row (e.g. a file or static-tree node).
CREATE TEMP TRIGGER IF NOT EXISTS node_detached_step_need_count AFTER UPDATE OF detached ON node
WHEN OLD.detached != NEW.detached AND EXISTS (SELECT 1 FROM step WHERE step.node = NEW.i)
BEGIN
    UPDATE step_need_count SET n = n - 1
    WHERE NEW.detached
        AND implied_need = (SELECT _implied_need FROM step WHERE step.node = NEW.i)
        AND succeeded = (
            SELECT state = {StepState.SUCCEEDED.value} FROM step WHERE step.node = NEW.i
        );
    INSERT INTO step_need_count(implied_need, succeeded, n)
    SELECT step._implied_need, step.state = {StepState.SUCCEEDED.value}, 1
    FROM step WHERE step.node = NEW.i AND NOT NEW.detached
    ON CONFLICT(implied_need, succeeded) DO UPDATE SET n = n + 1;
END;

-- Satellite tables below hold auxiliary per-step data. All are keyed by (or include) the
-- step's node and are removed via ON DELETE CASCADE when the node row is deleted.

-- Named glob pattern (with back-references) registered by this step; see NamedGlob.
-- pattern and regex must be derived from data by Python code performing the INSERT,
-- and are stored in columns of their own so that per-declaration and per-file-event checks
-- never have to deserialize data, whose match set is unbounded.
-- data is a JSON blob, see json_converter hooks for NamedGlob in cattrs.py.
CREATE TABLE IF NOT EXISTS nglob (
    i INTEGER PRIMARY KEY,
    node INTEGER NOT NULL,
    pattern TEXT NOT NULL,
    regex TEXT NOT NULL,
    data TEXT NOT NULL,
    FOREIGN KEY (node) REFERENCES node(i) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS nglob_node ON nglob(node);

-- Marks which dependency rows were dynamic (discovered while the step ran) rather than
-- declared upfront, so reset_for_rerun() knows which sources/sinks to drop between runs.
CREATE TABLE IF NOT EXISTS dynamic_dep (
    i INTEGER PRIMARY KEY,
    FOREIGN KEY (i) REFERENCES dependency(i) ON DELETE CASCADE
) WITHOUT ROWID;

-- Keep _check_ready in sync with dynamic-dependency changes: a dynamic/initial
-- dependency edge is evaluated differently by the "unavailable input" test
-- (see UNAVAILABLE_INPUT_WHERE above),
-- so flag the sink step whenever a dependency's dynamic status changes.
CREATE TRIGGER IF NOT EXISTS dynamic_dep_check_ready_ins AFTER INSERT ON dynamic_dep
BEGIN
    UPDATE step SET _check_ready = 1
    WHERE node = (SELECT sink FROM dependency WHERE i = NEW.i);
END;
CREATE TRIGGER IF NOT EXISTS dynamic_dep_check_ready_del AFTER DELETE ON dynamic_dep
BEGIN
    UPDATE step SET _check_ready = 1
    WHERE node = (SELECT sink FROM dependency WHERE i = OLD.i);
END;

-- Environment variable names each step depends on, and the value observed when recorded
-- (initial or dynamic).
CREATE TABLE IF NOT EXISTS env_var (
    node INTEGER NOT NULL,
    name TEXT NOT NULL,
    value TEXT,
    dynamic INTEGER NOT NULL CHECK(dynamic IN (0, 1)),
    PRIMARY KEY (node, name),
    -- The PRIMARY KEY above already indexes lookups by node (leftmost column),
    -- so no separate index on (node) is needed.
    FOREIGN KEY (node) REFERENCES node(i) ON DELETE CASCADE
) WITHOUT ROWID;

-- The stored hash of each step's last successful run, used to decide whether a rerun can be
-- skipped.
CREATE TABLE IF NOT EXISTS step_hash (
    node INTEGER PRIMARY KEY,
    hash TEXT NOT NULL,
    -- JSON-encoded StepHash of the last successful run.
    -- Absence of a row means no hash is stored (e.g. never run, or reset via Step.delete_hash).
    FOREIGN KEY (node) REFERENCES node(i) ON DELETE CASCADE,
    CHECK (json_valid(hash))
);

-- Keep _has_hash in sync with step_hash rows. Step.set_hash uses INSERT OR REPLACE, whose
-- implicit conflict-delete does not fire delete triggers but whose insert arm does, so
-- step_hash_ins alone correctly re-asserts _has_hash = 1 on overwrite.
CREATE TRIGGER IF NOT EXISTS step_hash_ins AFTER INSERT ON step_hash
BEGIN
    UPDATE step SET _has_hash = 1 WHERE node = NEW.node;
END;
CREATE TRIGGER IF NOT EXISTS step_hash_del AFTER DELETE ON step_hash
BEGIN
    UPDATE step SET _has_hash = 0 WHERE node = OLD.node;
END;

-- Outcome of the step's command, captured when it runs.
CREATE TABLE IF NOT EXISTS step_outcome (
    node INTEGER PRIMARY KEY,
    returncode INTEGER NOT NULL,
    -- The exit code of the step's command.
    stdout TEXT NOT NULL DEFAULT '',
    stderr TEXT NOT NULL DEFAULT '',
    -- Captured standard output/error of the step's command.
    -- Absence of a row means no output has been recorded for this run.
    utime REAL NOT NULL CHECK(utime >= 0) DEFAULT 0.0,
    stime REAL NOT NULL CHECK(stime >= 0) DEFAULT 0.0,
    wtime REAL NOT NULL CHECK(wtime >= 0) DEFAULT 0.0,
    -- Resource usage of the step's command: user/system/wall time in seconds.
    FOREIGN KEY (node) REFERENCES node(i) ON DELETE CASCADE
);

-- Named resource units (e.g. a semaphore-like GPU or license count) claimed by this step
-- while it runs; consumed by the scheduler to cap concurrent resource usage.
CREATE TABLE IF NOT EXISTS step_resource (
    node  INTEGER NOT NULL,
    name  TEXT    NOT NULL CHECK(name <> ''),
    units INTEGER NOT NULL CHECK(units > 0),
    PRIMARY KEY (node, name),
    FOREIGN KEY (node) REFERENCES node(i) ON DELETE CASCADE
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS step_resource_name ON step_resource(name);

-- Subprocess invocations made by this (wrapper) step, recorded for archival/debugging via
-- Step.add_subprocess; informative only, not authoritative.
CREATE TABLE IF NOT EXISTS step_subprocess (
    node INTEGER NOT NULL,
    -- The step node that ran this subprocess.
    cmd TEXT NOT NULL,
    -- The command line, as a single shell-quoted string.
    workdir TEXT NOT NULL DEFAULT './',
    -- The working directory of the subprocess, relative to STEPUP_ROOT.
    env_overrides TEXT,
    -- JSON-encoded dict[str, str] overlay of environment variables set on top of the
    -- inherited environment, or NULL when no overlay was applied.
    returncode INTEGER NOT NULL,
    -- The exit code of the subprocess.
    shell INTEGER NOT NULL DEFAULT 0,
    -- Whether cmd was executed via a shell (1) or directly (0).
    stdin TEXT,
    -- The standard input fed to the subprocess, or NULL.
    stdout TEXT,
    -- The captured standard output of the subprocess, or NULL if not captured.
    stderr TEXT,
    -- The captured standard error of the subprocess, or NULL if not captured.
    -- ON DELETE CASCADE removes these rows when the node row is deleted, matching the
    -- other satellite tables (env_var / step_hash / step_outcome / step_resource).
    -- Step.reset_for_rerun() still clears them explicitly between runs of a surviving step.
    FOREIGN KEY (node) REFERENCES node(i) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS step_subprocess_node ON step_subprocess(node);
"""


def truncate_output(content: str, max_bytes: int) -> str:
    """Truncate `content` to at most `max_bytes` UTF-8 bytes, appending a sentinel if cut.

    A `max_bytes` of `0` (or any non-positive value) means unlimited: the content is
    returned unchanged. Otherwise the cut is made on a valid UTF-8 character boundary
    (`decode(..., "ignore")` drops a trailing partial multi-byte sequence), so the result
    is always valid text.

    Parameters
    ----------
    content
        The captured text to (possibly) truncate.
    max_bytes
        Maximum number of UTF-8 bytes to keep, or `0` (or any non-positive value) for
        unlimited.

    Returns
    -------
    truncated
        The original content if within the budget or unlimited, otherwise the content cut
        to `max_bytes` bytes with a sentinel line appended.
    """
    if max_bytes <= 0:
        return content
    encoded = content.encode("utf-8")
    if len(encoded) <= max_bytes:
        return content
    truncated = encoded[:max_bytes].decode("utf-8", "ignore")
    return f"{truncated}\n[output truncated at {max_bytes} bytes]\n"


# When a step is detached or recycled, its creator chain changes, which alters the "safe" state
# of the step and of every step it created (recursively): whether their (indirect) creator is in
# a state that allows queuing them. Flag _check_safe (and _check_after) on the step and all its
# product steps so the scheduler recomputes their metadata.
RECURSIVE_CHECK_WITH_PRODUCTS = """
UPDATE step SET _check_safe = 1, _check_after = 1 FROM (
    WITH RECURSIVE check_with_products(node) AS (
        -- Fairly trivial initialization, will only work if the node is a step.
        SELECT node FROM step WHERE node = ?
        UNION ALL
        -- Recurse over all product steps of the step.
        -- Products that are not steps can be ignored.
        SELECT i FROM node
        JOIN check_with_products ON node.creator = check_with_products.node
        WHERE node.kind = 'step'
    )
    SELECT node FROM check_with_products
) AS cwp WHERE step.node = cwp.node
"""


# When a step (sub)tree is detached, the steps supplying inputs to it lose a sink.
# Their _implied_need and _tail_time must therefore be recomputed, so flag _check_after on them.
# Sources are reached with two dependency hops: subtree_step <- input_file <- source_step.
# Only non-detached source steps are flagged; detached ones are excluded from the metadata anyway.
RECURSIVE_CHECK_AFTER_SOURCES = """
UPDATE step SET _check_after = 1 FROM (
    WITH RECURSIVE subtree(node) AS (
        -- Start from the detached step and recurse over its product steps (the detached subtree).
        SELECT node FROM step WHERE node = ?
        UNION ALL
        SELECT i FROM node
        JOIN subtree ON node.creator = subtree.node
        WHERE node.kind = 'step'
    )
    -- Two hops back along dependency edges to the source steps of the subtree.
    SELECT DISTINCT dep2.source AS node
    FROM subtree
    JOIN dependency AS dep1 ON dep1.sink = subtree.node
    JOIN dependency AS dep2 ON dep2.sink = dep1.source
    JOIN node AS source_node ON source_node.i = dep2.source
    WHERE source_node.kind = 'step' AND NOT source_node.detached
) AS sup WHERE step.node = sup.node
"""


@attrs.define(frozen=True)
class PathRecord:
    """One path yielded by `Step._paths()` and its wrapper methods."""

    path: str
    """The path, relative to the workflow root."""

    state: FileState
    """The state of the file."""

    detached: bool
    """Whether the path is detached, i.e. its creating step moved on without it."""

    dynamic: bool
    """Whether the path was a dynamic dependency, i.e. discovered while the step was running."""

    _hash_json: str | None = attrs.field(repr=False)
    """The JSON representation of the file's hash, decoded lazily through `hash`."""

    @property
    def hash(self) -> FileHash:
        """The file's hash, lazily decoded from its JSON representation."""
        return FileHash.from_json(self._hash_json)


@attrs.define
class Step(Node):
    """A command in the workflow with its input and/or output files."""

    #
    # Override from base class
    #

    @classmethod
    def schema(cls) -> str | None:
        """Return node-specific SQL commands to initialize the database."""
        return STEP_SCHEMA

    @classmethod
    def adjust_label(cls, label: str, workdir: str = ".", **kwargs):
        """Derive the step label from the command and optional working directory."""
        if "  # wd=" in label:
            raise ValueError(
                "Do not include a workdir comment in the command string. "
                "Pass the workdir separately."
            )
        if workdir != ".":
            label += f"  # wd={workdir}"
        return label

    def initialize_row(
        self,
        *,
        need: Need = Need.DEFAULT,
        shell: bool = False,
        duration: float | None = None,
        _safe: bool = False,
        **kwargs,  # workdir is consumed by adjust_label, not used here
    ):
        """Create extra information in the database about this node.

        Parameters
        ----------
        need
            The need of the step, as defined in the Need enum.
        shell
            Whether the step command is executed via a shell (shell=True).
        duration
            An estimate of the wall time of the step in seconds. If not given, defaults to 1.0.
        _safe
            Whether this step is safe to run, meaning that all its (recursive) creators
            are in a state that allows queuing this step (RUNNING or SUCCEEDED).
        """
        # `DELETE` before an `INSERT` instead of `INSERT OR REPLACE`,
        # so the delete fires `step_need_count_del` like any other delete
        # instead of being silently skipped by `REPLACE`'s implicit conflict-delete
        # (which never fires delete triggers).
        self.db.execute("DELETE FROM step WHERE node = :node", {"node": self.i})

        # The `step_hash`/`step_outcome` satellite rows are untouched
        # by either `DELETE` or `INSERT`, since both only ever reference `node`, not `step`,
        # so a recycled step's stored hash remains available for
        # skip-checking after redeclaration instead of being discarded.
        # `_has_hash` must therefore be set explicitly here (rather than left to its `DEFAULT 0`),
        # since this statement never touches the `step_hash` table itself.

        # `_ready`/`_check_ready` need no explicit value.
        # Their `DEFAULT`s (0 and 1) are exactly the conservative
        # "not yet known, must be recomputed" state a new/recycled step should start in.

        # `_check_after` is always seeded to `1`, even though a fresh `OPTIONAL` step's
        # `_implied_need` already equals its seeded value (`OPTIONAL` is `Need`'s minimum, so there
        # is nothing to recompute), *unless* this is `Trellis.create()`'s partial-recycle branch
        # (`if node is not None:`), which cuts the node's sources but keeps its sinks.
        # A recycled `OPTIONAL` step can then inherit a stale sink whose downstream consumer would
        # elevate its true `_implied_need`, and nothing forces the recompute unless a new dependency
        # insert happens to touch this node afterward (see `step_dependency_check_after_ins`).
        # Seeding `1` unconditionally costs one extra (usually no-op) row in the first iteration of
        # `Scheduler._update_meta_after()`, in exchange for not depending on that unstated
        # call-order assumption.

        # `duration` falls back to `1.0` (matching the column's own `DEFAULT`) when not given,
        # since a brand-new step has no prior measurement to seed it with.

        # `_safe_ignoring_hold` is seeded to the same value as `_safe`
        # (rather than left at its own `DEFAULT 0`).
        # A step created with `safe=True` (in practice, only the root `plan.py` step)
        # also gets `_check_safe = 0` at creation (not flagged for recompute),
        # unlike an ordinary step (`safe=False`, hence `check_safe = 1`, flagged immediately).
        # Without this seeding, the root step's `_safe_ignoring_hold` would sit at its unseeded
        # `DEFAULT 0` until its own state next changes and re-flags `_check_safe`.
        # Every top-level step's `_safe_ignoring_hold` chain is seeded from the root's,
        # via `creator_step._safe_ignoring_hold` in `SELECT_SAFE_UPDATE`.
        self.db.execute(
            "INSERT INTO step "
            "(node, state, need, duration, shell, _safe, _check_safe, _safe_ignoring_hold, "
            "_implied_need, _check_after, _has_hash) "
            "VALUES(:node, :state, :need, :duration, :shell, :safe, :check_safe, :safe, "
            ":implied_need, 1, "
            "(SELECT EXISTS(SELECT 1 FROM step_hash WHERE node = :node)))",
            {
                "node": self.i,
                "need": need.value,
                "state": StepState.PENDING.value,
                "duration": 1.0 if duration is None else duration,
                "shell": int(shell),
                "safe": int(_safe),
                "check_safe": int(not _safe),
                "implied_need": need.value,
            },
        )

    def validate_row(self):
        """Validate that extra information about this node is present in the database."""
        row = self.db.execute("SELECT 1 FROM step WHERE node = ?", (self.i,)).fetchone()
        if row is None:
            raise ValueError(f"Step node {self.key()} has no row in the step table.")

    def format_properties(self) -> Iterator[tuple[str, str]]:
        """Iterate over key-value pairs that represent the properties of the node."""
        sql = "SELECT state, need, _implied_need FROM step WHERE node = ?"
        state_id, need_id, implied_need_id = self.db.execute(sql, (self.i,)).fetchone()
        state = StepState(state_id)
        yield "state", state.name
        need = Need(need_id)
        implied_need = Need(implied_need_id)
        if need == implied_need:
            yield "need", need.name
        else:
            yield "need", f"{implied_need.name} (implied by sinks > {need.name})"

        sql = "SELECT name, dynamic FROM env_var WHERE node = ?"
        label = "using_env"
        for env_var, dynamic in self.db.execute(sql, (self.i,)):
            yield label, f"{env_var} [dynamic]" if dynamic else env_var
            label = ""

        label = "env_overrides"
        for name, value in sorted(self.get_env_overrides().items()):
            yield label, f"{name}={value}"
            label = ""

        for row in self.db.execute("SELECT data FROM nglob WHERE node = ?", (self.i,)):
            ng = json_converter.structure(json.loads(row[0]), NamedGlob)
            line = ng.pattern
            if len(ng.subs) > 0:
                line += " (" + " ".join(f"{k}={v}" for k, v in ng.subs.items()) + ")"
            yield "nglob", line

        for name, units in self.resources():
            yield "resource", f"{name}: {units} units"

        step_hash = self.get_hash()
        if step_hash is not None:
            yield "inp_digest", format_digest(step_hash.inp_digest)
            yield "out_digest", format_digest(step_hash.out_digest)
            if step_hash.inp_info is not None:
                yield "explained", "yes"

    def after_lost_product(self):
        """Invalidate the step hash because a product of this detached step was lost.

        A product is lost when `Trellis.delete_detached` deletes it
        or when another creator takes it over.
        The step itself stays recyclable, but its stored hash no longer describes
        a complete run: skipping the step would leave the lost products unbuilt.
        Without a hash, a recycled step always runs again and recreates them.
        """
        self.delete_hash()

    def before_delete(self):
        """Queue the working directory for removal right before the step is deleted.

        StepUp creates the working directory when it dispatches the step,
        so it also takes it away again when the step disappears from the workflow.
        The directory is only removed when it is empty by the end of the cleanup pass.
        """
        self.graph.mark_dir_to_be_deleted(self.command_and_workdir[1])

    def can_recycle(
        self,
        *,
        inp_paths: Collection[str] = (),
        env_deps: Collection[str] = (),
        out_paths: Collection[str] = (),
        vol_paths: Collection[str] = (),
        **kwargs,
    ) -> bool:
        """Decide whether this detached step may be fully recycled by `Trellis.try_recycle`.

        A detached step can only be recycled when the initial (non-dynamic) inputs,
        environment variables and (volatile) outputs of the new declaration
        match those of the detached step exactly.
        """
        old_inp_paths = sorted(r.path for r in self.inp_paths(dynamic=False))
        if old_inp_paths != sorted(inp_paths):
            return False
        old_env_vars = sorted(self.env_deps(dynamic=False))
        if old_env_vars != sorted(env_deps):
            return False
        old_out_paths = sorted(r.path for r in self.out_paths(dynamic=False))
        if old_out_paths != sorted(out_paths):
            return False
        old_vol_paths = sorted(r.path for r in self.vol_paths(dynamic=False))
        return old_vol_paths == sorted(vol_paths)

    def after_recycle(
        self,
        *,
        need: Need = Need.DEFAULT,
        shell: bool = False,
        resources: dict[str, int] | None = None,
        env_overrides: dict[str, str] | None = None,
        duration: float | None = None,
        **kwargs,
    ):
        """Update the mutable declared properties of this step after a full recycle.

        The step keeps its state and stored hash,
        so it can still be skipped when its inputs have not changed.
        Other declaration arguments (`safe` and the path lists) are deliberately ignored:
        the path lists were verified by `can_recycle`
        and the `_safe` metadata is recomputed by the scheduler
        via the `_check_safe` flag set by `Step.reattach`.

        `duration`, when `None`, deliberately leaves the recycled step's existing duration
        (its previous measurement, if any) untouched, unlike a brand-new step's default.

        `_holding` is always reset to 0: a recycled step cannot still be inside a `hold()`
        block from a previous run, since that block would have released it (or failed)
        before the step could be recycled.

        A FAILED step is the one state that is not carried over: it is made PENDING so the
        recycled step is retried. A failed step is never skippable anyway (it has no stored
        hash), so keeping the state would only park it in a state nothing takes it out of
        within the same build, while `report_completion` still counts it as a failure.
        """
        self.db.execute(
            "UPDATE step SET need = ?, shell = ?, _holding = 0 WHERE node = ?",
            (need.value, int(shell), self.i),
        )
        if self.get_state() == StepState.FAILED:
            self.graph.mark_step_pending(self)
        self.set_resources(resources)
        self.set_env_overrides(env_overrides)
        if duration is not None:
            self.set_duration(duration)

    #
    # Getters and setters
    #

    def _dependency_keys(
        self,
        node_type: type[NodeType] | None = None,
        *,
        upstream: bool,
    ) -> Iterator[str]:
        """Iterate over sinks, or over sources when `upstream` is `True`, as `kind:label` keys.

        Detached edges are wrapped in parentheses
        and dynamic edges get an `" [dynamic]"` suffix.
        """
        sql = (
            "SELECT kind, label, detached, dependency.i IN dynamic_dep "
            "FROM node JOIN dependency ON node.i = "
        ) + ("source WHERE sink = ?" if upstream else "sink WHERE source = ?")
        data = [self.i]
        if node_type is not None:
            sql += " AND kind = ?"
            data.append(node_type.kind())
        sql += " ORDER BY kind, label"
        for kind, label, detached, dynamic in self.db.execute(sql, data):
            key = f"{kind}:{label}"
            if detached:
                key = f"({key})"
            if dynamic:
                key += " [dynamic]"
            yield key

    @property
    def command_and_workdir(self) -> tuple[str, Path]:
        """The command and working directory of this step."""
        parts = self.label.split("  # wd=", maxsplit=1)
        return parts[0], Path(parts[1] if len(parts) == 2 else ".")

    def get_info(self) -> StepInfo:
        """Return a `StepInfo` object with information about this step.

        Dynamic dependencies are not included for consistency with
        the information that is available when defining a step.

        A detached step still describes its own declaration (see `Step._paths`).
        """
        command, workdir = self.command_and_workdir
        return StepInfo(
            command,
            [r.path for r in self.inp_paths(dynamic=False)],
            self.env_deps(dynamic=False),
            [r.path for r in self.out_paths(dynamic=False)],
            [r.path for r in self.vol_paths(dynamic=False)],
            workdir,
        )

    def uses_shell(self) -> bool:
        """Return whether this step runs the command via a shell."""
        row = self.db.execute("SELECT shell FROM step WHERE node = ?", (self.i,)).fetchone()
        return bool(row[0])

    def get_need(self) -> Need:
        """Return the declared need of this step."""
        row = self.db.execute("SELECT need FROM step WHERE node = ?", (self.i,)).fetchone()
        return Need(row[0])

    def get_state(self) -> StepState:
        """Return the current state of this step."""
        row = self.db.execute("SELECT state FROM step WHERE node = ?", (self.i,)).fetchone()
        return StepState(row[0])

    def set_state(self, state: StepState, deferred: bool = False) -> None:
        """Set the state of this step."""
        # deferred=True combined with a state other than PENDING is rejected by the
        # step table's deferred/state CHECK constraint (see STEP_SCHEMA).
        self.db.execute(
            "UPDATE step SET state = ?, deferred = ? WHERE node = ?",
            (state.value, deferred, self.i),
        )

    def has_unavailable_dynamic_input(self) -> bool:
        """Determine if any dynamic input dependency is not currently `CONFIRMED` or `BUILT`."""
        sql = f"""
        SELECT EXISTS (
            SELECT 1 FROM dependency
            JOIN dynamic_dep ON dynamic_dep.i = dependency.i
            JOIN file ON file.node = dependency.source
            WHERE dependency.sink = ?
            AND file.state NOT IN ({FileState.CONFIRMED.value}, {FileState.BUILT.value})
        )
        """
        return bool(self.db.execute(sql, (self.i,)).fetchone()[0])

    def get_defer_count(self) -> int:
        """Return the number of consecutive defers since the last SUCCEEDED state."""
        sql = "SELECT defer_count FROM step WHERE node = ?"
        return self.db.execute(sql, (self.i,)).fetchone()[0]

    def set_duration(self, duration: float) -> None:
        """Set the estimated duration (in seconds) of this step."""
        self.db.execute("UPDATE step SET duration = ? WHERE node = ?", (duration, self.i))

    def is_holding(self) -> bool:
        """Return whether this step is currently holding back its descendant steps from dispatch."""
        row = self.db.execute("SELECT _holding FROM step WHERE node = ?", (self.i,)).fetchone()
        return row[0] > 0

    def hold(self):
        """Hold back this step's descendant steps from dispatch until a matching `release()`.

        Re-entrant: nested `hold()` calls on the same step increment a counter, and descendants
        stay held back until the outermost `release()` brings the counter back to zero.
        """
        row = self.db.execute(
            "UPDATE step SET _holding = _holding + 1 WHERE node = ? RETURNING _holding", (self.i,)
        ).fetchone()
        if row[0] == 1:
            # Only a 0 -> 1 transition can newly block any descendant's dispatch eligibility;
            # nested hold() calls on an already-holding step change nothing observable.
            self._flag_checks_with_products()

    def release(self):
        """Release one `hold()` on this step, decrementing its open-hold counter.

        Raises
        ------
        GraphError
            If this step is not currently holding, i.e. `release()` was called more often
            than `hold()`.
        """
        row = self.db.execute(
            "UPDATE step SET _holding = _holding - 1 WHERE node = ? AND _holding > 0 "
            "RETURNING _holding",
            (self.i,),
        ).fetchone()
        if row is None:
            raise GraphError(f"Step {self.key()} is not holding; release() has no matching hold().")
        if row[0] == 0:
            # Only a 1 -> 0 transition can newly unblock any descendant's dispatch eligibility.
            self._flag_checks_with_products()

    #
    # Env vars
    #

    def get_env_overrides(self) -> dict[str, str]:
        """Return the step-specific environment variable overrides."""
        row = self.db.execute("SELECT env_overrides FROM step WHERE node = ?", (self.i,)).fetchone()
        return {} if row[0] is None else json.loads(row[0])

    def set_env_overrides(self, env_overrides: dict[str, str] | None):
        """Set the step-specific environment variable overrides."""
        value = None if not env_overrides else json.dumps(env_overrides)
        self.db.execute("UPDATE step SET env_overrides = ? WHERE node = ?", (value, self.i))

    def set_resources(self, resources: dict[str, int] | None) -> None:
        """Replace the named-resource units claimed by this step.

        `resources` maps resource name (e.g. a GPU or license semaphore) to
        the number of units claimed, or is `None` to clear all claims.
        """
        self.db.execute("DELETE FROM step_resource WHERE node = ?", (self.i,))
        if resources is None:
            return
        rows = [(self.i, name, units) for name, units in resources.items()]
        self.db.executemany("INSERT INTO step_resource VALUES (?, ?, ?)", rows)

    def add_env_deps(self, env_deps: Collection[str]) -> None:
        """Record environment variables read by this step, declared up front.

        The current `os.getenv` value of each name is stored alongside it.
        """
        rows = [(self.i, name, os.getenv(name)) for name in env_deps]
        self.db.executemany("INSERT OR REPLACE INTO env_var VALUES (?, ?, ?, 0)", rows)

    def amend_env_deps(self, env_deps: Collection[str]) -> None:
        """Record environment variables read by this step, discovered while running.

        The current `os.getenv` value of each name is stored alongside it.
        Names already present in `env_overrides` are skipped,
        since their value is fixed by the step
        and they are therefore not external dependencies that can change between runs.
        """
        env_overrides = self.get_env_overrides()
        rows = [(self.i, name, os.getenv(name)) for name in env_deps if name not in env_overrides]
        self.db.executemany("INSERT OR IGNORE INTO env_var VALUES (?, ?, ?, 1)", rows)

    def env_deps(self, *, dynamic: bool | None = None) -> Iterator[str]:
        """Iterate over used environment variable names (not values)."""
        sql = "SELECT name FROM env_var WHERE node = ?"
        if dynamic is not None:
            sql += " AND"
            if not dynamic:
                sql += " NOT"
            sql += " dynamic = 1"
        for row in self.db.execute(sql, (self.i,)):
            yield row[0]

    #
    # Iterators
    #

    def _paths(
        self,
        relation: Literal["product", "source", "sink"],
        *,
        raw: bool = False,
        dynamic: bool | None = None,
        states: Collection[FileState] = (),
    ) -> Iterator[PathRecord]:
        """Iterate over paths of this step using various criteria.

        A path can carry the `detached` flag for two unrelated reasons:

        1. This step is detached, which detaches everything it created with it
           (outputs, volatile outputs, static declarations).
        2. The path's own creator detached it, which says nothing about this step.
           An input that nothing declares is born detached, and a static declaration is detached
           when the step that declared it reruns.

        Only the first kind is about this step, so a path is yielded when it is attached or when
        this step is detached. Always excluding detached paths would hide a detached step's own
        outputs, and always including them would let an attached step pick up paths that left
        the graph without it. With this rule, the wrappers of this method all mean the same
        thing, namely what this step declares, whether or not the step is still attached.

        Only a source can be detached without this step: a product is selected by its creator,
        and an output is created by this step and only ever detached together with it.

        Parameters
        ----------
        relation
            Which paths to iterate over: those created by this step (`product`),
            or those connected to it by a dependency edge (`source` or `sink`).
        raw
            Yield every path row, whatever its `detached` flag, bypassing the rule above.
        dynamic
            Restrict to paths discovered while the step was running (`True`),
            or to those known when the step was defined (`False`).
        states
            Restrict to files in one of these states. All states when empty.
        """
        # Which relation?
        data = {"node": self.i}
        if relation == "product":
            # There is no dependency row for a product, so `idep` is NULL, which makes the
            # dynamic-exists check below (and a `dynamic=True` filter) naturally resolve to
            # "never amended".
            # This is correct, since a declared static/missing path can't be amended.
            sql = (
                "WITH relevant AS (SELECT i AS node, NULL AS idep FROM node WHERE creator = :node)"
            )
        elif relation == "source":
            sql = (
                "WITH relevant AS "
                "(SELECT source AS node, i AS idep FROM dependency WHERE sink = :node)"
            )
        elif relation == "sink":
            sql = (
                "WITH relevant AS "
                "(SELECT sink AS node, i AS idep FROM dependency WHERE source = :node)"
            )
        else:
            raise ValueError(f"Unrecognized relation argument: '{relation}'")
        join = "JOIN node ON node.i = relevant.node JOIN file ON file.node = relevant.node"
        fields = [
            "label",
            "state",
            "hash",
            "detached",
            "EXISTS (SELECT 1 FROM dynamic_dep WHERE dynamic_dep.i = relevant.idep)",
        ]
        where = "WHERE kind = 'file'"

        # Exclude paths detached without this step (see above).
        if not (raw or self.is_detached()):
            where += " AND NOT detached"

        # Restrict to dynamic or to initial (non-dynamic) files.
        if dynamic is not None:
            if dynamic:
                join += " JOIN dynamic_dep ON dynamic_dep.i = relevant.idep"
            else:
                where += (
                    " AND NOT EXISTS (SELECT 1 FROM dynamic_dep"
                    " WHERE dynamic_dep.i = relevant.idep)"
                )

        # Filter certain states.
        if len(states) > 0:
            where_states = []
            for i, state in enumerate(states):
                where_states.append(f"state = :state_{i}")
                data[f"state_{i}"] = state.value
            where += f" AND ({' OR '.join(where_states)})"

        sql += f" SELECT {', '.join(fields)} FROM relevant {join} {where}"
        for label, state, hash_json, detached, is_dynamic in self.db.execute(sql, data):
            yield PathRecord(label, FileState(state), bool(detached), bool(is_dynamic), hash_json)

    def inp_paths(self, *, dynamic: bool | None = None) -> Iterator[PathRecord]:
        """Iterate over input files of this step."""
        yield from self._paths("source", dynamic=dynamic)

    def out_paths(self, *, dynamic: bool | None = None) -> Iterator[PathRecord]:
        """Iterate over output files of this step."""
        yield from self._paths("sink", dynamic=dynamic, states=FILE_STATES_BY_ROLE[FileRole.OUTPUT])

    def vol_paths(self, *, dynamic: bool | None = None) -> Iterator[PathRecord]:
        """Iterate over volatile output files of this step."""
        yield from self._paths(
            "sink", dynamic=dynamic, states=FILE_STATES_BY_ROLE[FileRole.VOLATILE]
        )

    def static_paths(self) -> Iterator[PathRecord]:
        """Iterate over static paths created by this step."""
        yield from self._paths("product", states=(FileState.CONFIRMED,))

    def missing_paths(self) -> Iterator[PathRecord]:
        """Iterate over missing paths created by this step."""
        yield from self._paths("product", states=(FileState.MISSING,))

    def nglobs(self) -> Iterator[NamedGlob]:
        """Iterate over nglobs used by this step."""
        for row in self.db.execute("SELECT data FROM nglob WHERE node = ?", (self.i,)):
            yield json_converter.structure(json.loads(row[0]), NamedGlob)

    def resources(self) -> Iterator[tuple[str, int]]:
        """Iterate over the `(name, units)` pairs of the resources required by this step."""
        yield from self.db.execute(
            "SELECT name, units FROM step_resource WHERE node = ?", (self.i,)
        )

    #
    # Build phase
    #

    def reset_for_rerun(self):
        """Reset a step back to its freshly defined state, ready to run again.

        Safe to call on a step that has never run.
        Useful to call on a step that has not fully completed,
        e.g. because it was interrupted or has failed.

        The following are dropped:

        - dynamic inputs and (volatile) outputs
        - dynamic environment variables
        - nglobs
        - stored stdout/stderr and recorded subprocess invocations

        The following are detached:

        - created steps
        - static file definitions
        - static trees

        The following are marked as outdated:

        - output files that are in state BUILT
        """
        # Drop dynamic sources.
        rows = list(
            self.db.execute(
                "SELECT dependency.i, node.i, node.label, node.kind FROM dependency "
                "JOIN node ON node.i = source "
                "JOIN dynamic_dep ON dynamic_dep.i = dependency.i WHERE sink = ?",
                (self.i,),
            )
        )
        self.db.executemany("DELETE FROM dynamic_dep WHERE i = ?", ((row[0],) for row in rows))
        self.del_sources([self.graph.node_from_row(i, kind, label) for _, i, label, kind in rows])

        # Drop dynamic environment variables.
        self.db.execute("DELETE FROM env_var WHERE node = ? AND dynamic = 1", (self.i,))

        # Drop nglobs.
        self.db.execute("DELETE FROM nglob WHERE node = ?", (self.i,))

        # Drop dynamic sinks and detach the corresponding sink nodes.
        records_sink = list(
            self.db.execute(
                "SELECT dependency.i, sink, label, kind FROM dependency "
                "JOIN dynamic_dep ON dynamic_dep.i = dependency.i "
                "JOIN node ON sink = node.i "
                "WHERE source = ?",
                (self.i,),
            )
        )
        ideps_sink = [(row[0],) for row in records_sink]
        self.db.executemany("DELETE FROM dynamic_dep WHERE i = ?", ideps_sink)
        for _, i, label, kind in records_sink:
            node = self.graph.node_from_row(i, kind, label)
            node.del_sources([self])
            node.detach()

        self._detach_created_steps()

        # Detach static file definitions.
        states = ", ".join(str(state.value) for state in FILE_STATES_BY_ROLE[FileRole.STATIC])
        sql = (
            "SELECT i, label FROM node JOIN file ON node.i = file.node "
            f"WHERE creator = ? AND state IN ({states})"
        )
        for i, label in self.db.execute(sql, (self.i,)):
            file = File(self.graph, i, label)
            file.detach()

        # Detach static trees.
        sql = "SELECT i, label FROM node WHERE creator = ? AND kind = 'st'"
        for i, label in self.db.execute(sql, (self.i,)):
            st = StaticTree(self.graph, i, label)
            st.detach()

        # Mark BUILT outputs OUTDATED.
        sql = (
            "SELECT i, label FROM node JOIN file ON node.i = file.node "
            "WHERE creator = ? AND state = ?"
        )
        data = (self.i, FileState.BUILT.value)
        for i, label in self.db.execute(sql, data):
            file = File(self.graph, i, label)
            self.graph.mark_file_outdated(file)

        # Drop any output stored by a previous run.
        self.delete_outcome()

        # Drop any subprocess invocations recorded by a previous run.
        self.delete_subprocesses()

    def _detach_created_steps(self):
        """Detach steps created by this step (e.g. via `run()`/`step()`).

        Called unconditionally by `reset_for_rerun()`,
        and by `mark_completed()` only when a step reaches a genuine terminal `FAILED` state
        (not on an accepted defer):
        the failed run's product steps must not linger attached even before the creator's
        actual rerun happens, which may be much later.
        Unlike `reset_for_rerun()`, this does not touch dynamic dependencies,
        so a defer triggered by an unavailable dynamic input does not sever the dependency edge
        that `Workflow.mark_step_pending()` relies on to wake the step up again
        once that input becomes available.

        A still-`RUNNING` detached step is not killed: it keeps running until its
        command terminates on its own, and is then recorded and reported like any
        other step. If such a command never terminates, the build hangs;
        this is a deliberate trade-off, not an oversight.
        """
        sql = "SELECT i, label FROM node WHERE creator = ? AND kind = 'step'"
        for i, label in self.db.execute(sql, (self.i,)):
            step = Step(self.graph, i, label)
            step.detach()

    def _increment_defer_count(self) -> int:
        """Increment defer_count and return the new value."""
        row = self.db.execute(
            "UPDATE step SET defer_count = defer_count + 1 WHERE node = ? RETURNING defer_count",
            (self.i,),
        ).fetchone()
        return row[0]

    def mark_completed(self, new_hash: StepHash | None, wants_defer: bool) -> bool:
        """Set a step as completed (succeeded or failed) and trigger the consequences.

        Whether the step is detached makes no difference here:
        a detached step is recorded exactly like an attached one,
        so that it can be skipped when its creator recreates it identically.

        Parameters
        ----------
        new_hash
            The new digest of the completed step if the step was successful, `None` otherwise.
        wants_defer
            True if the step wants to be deferred, False otherwise.

        Returns
        -------
        interrupted_defer
            True if deferral has been interrupted because the cap was exceeded, False otherwise.
        """
        interrupted_defer = False
        if new_hash is None:
            # Update states, needed for files that have not changed since the previous run.
            for file in self.products(File):
                if file.get_state() == FileState.BUILT:
                    file.set_state(FileState.OUTDATED)
            if wants_defer:
                defer_count = self._increment_defer_count()
                if defer_count <= self.graph.defer_cap:
                    logger.info(
                        "Deferred step (%d/%d): %s",
                        defer_count,
                        self.graph.defer_cap,
                        self.label,
                    )
                    # The state is set to PENDING below.
                    # However, the step will not be scheduled as long as `deferred` is True.
                    # Any later file changes relevant to the step result in
                    # a call to Workflow.mark_step_pending(), which clears the deferred flag.
                    # This makes the step eligible for scheduling again.
                    deferred = self.has_unavailable_dynamic_input()
                    self.set_state(StepState.PENDING, deferred)
                else:
                    logger.info(
                        "Defer cap (%d) exceeded, failed step: %s",
                        self.graph.defer_cap,
                        self.label,
                    )
                    self.set_state(StepState.FAILED)
                    interrupted_defer = True
            else:
                logger.info("Failed step: %s", self.label)
                self.set_state(StepState.FAILED)
            if self.get_state() == StepState.FAILED:
                # The step may have created product steps that are already running
                # opportunistically. A genuine terminal failure detaches all of them;
                # an accepted defer (state stays PENDING) does not, since this step
                # will run again soon and its products should stay attached until then.
                self._detach_created_steps()
            # An unsuccessful step is not skippable, so we're removing its hash.
            self.delete_hash()
        else:
            logger.info("Succeeded step: %s", self.label)
            self.set_state(StepState.SUCCEEDED)
            # Update states, needed for files that have not changed since the previous run.
            for file in self.products(File):
                if file.get_state() == FileState.OUTDATED:
                    file.set_state(FileState.BUILT)
                    self.graph.mark_consuming_steps_pending(file)
            self.set_hash(new_hash)
        return interrupted_defer

    def get_hash(self) -> StepHash | None:
        """Return the stored step hash, or `None` if none is stored."""
        row = self.db.execute("SELECT hash FROM step_hash WHERE node = ?", (self.i,)).fetchone()
        return None if row is None else StepHash.from_json(row[0])

    def set_hash(self, step_hash: StepHash):
        """Store the step hash."""
        self.db.execute(
            "INSERT OR REPLACE INTO step_hash VALUES (?, ?)", (self.i, step_hash.to_json())
        )

    def delete_hash(self):
        """Clear the stored step hash, if any."""
        self.db.execute("DELETE FROM step_hash WHERE node = ?", (self.i,))

    def set_outcome(self, outcome: ChildOutcome, max_bytes: int) -> None:
        """Persist captured child process outcome for this step in a single update.

        Parameters
        ----------
        outcome
            The child outcome to store.
        max_bytes
            Maximum number of UTF-8 bytes to store per stream, or `0` for unlimited.
            See `truncate_output`.
        """
        self.db.execute(
            "INSERT OR REPLACE INTO step_outcome VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                self.i,
                outcome.returncode,
                truncate_output(outcome.stdout, max_bytes),
                truncate_output(outcome.stderr, max_bytes),
                outcome.usage.utime,
                outcome.usage.stime,
                outcome.usage.wtime,
            ),
        )

    def get_outcome(self) -> ChildOutcome | None:
        """Return the stored child outcome for this step."""
        row = self.db.execute(
            "SELECT returncode, stdout, stderr, utime, stime, wtime "
            "FROM step_outcome WHERE node = ?",
            (self.i,),
        ).fetchone()
        if row is None:
            return None
        return ChildOutcome(
            returncode=row[0],
            stdout=row[1],
            stderr=row[2],
            usage=ResourceUsage(utime=row[3], stime=row[4], wtime=row[5]),
        )

    def delete_outcome(self) -> None:
        """Remove the stored child outcome for this step."""
        self.db.execute("DELETE FROM step_outcome WHERE node = ?", (self.i,))

    def add_subprocess(
        self,
        cmd: str,
        workdir: str,
        env_overrides: dict[str, str] | None,
        returncode: int,
        shell: bool,
        stdin: str,
        stdout: str,
        stderr: str,
    ) -> None:
        """Record a subprocess invocation made by this (wrapper) step.

        The recorded metadata is informative for archival and debugging, not authoritative.
        The `env_overrides` overlay holds only the variables the wrapper explicitly set
        on top of the inherited environment, not the full resolved environment.

        Parameters
        ----------
        cmd
            The command line, as a single shell-quoted string, stored verbatim.
        workdir
            The working directory of the subprocess, relative to `STEPUP_ROOT`.
        env_overrides
            The environment overlay (variables set on top of the inherited environment),
            or `None` when no overlay was applied.
        returncode
            The exit code of the subprocess.
        shell
            Whether `cmd` was executed via a shell (`subprocess.run(..., shell=True)`).
        stdin
            The standard input fed to the subprocess as a string.
        stdout, stderr
            The captured standard output/error of the subprocess as a string.
        """
        # Invocation order is preserved by the table's rowid insertion order, so no
        # separate sequence number needs to be looked up or assigned here.
        self.db.execute(
            "INSERT INTO step_subprocess "
            "(node, cmd, workdir, env_overrides, returncode, shell, stdin, stdout, stderr) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                self.i,
                cmd,
                workdir,
                None if env_overrides is None else json.dumps(env_overrides),
                returncode,
                int(shell),
                stdin,
                stdout,
                stderr,
            ),
        )

    def delete_subprocesses(self) -> None:
        """Remove all recorded subprocess rows for this step."""
        self.db.execute("DELETE FROM step_subprocess WHERE node = ?", (self.i,))

    def add_nglob(self, ng: NamedGlob) -> None:
        """Store a `NamedGlob` pattern registered by this step."""
        data = (
            self.i,
            ng.pattern,
            convert_nglob_to_regex(ng.pattern, ng.subs),
            json.dumps(json_converter.unstructure(ng)),
        )
        self.db.execute("INSERT INTO nglob(node, pattern, regex, data) VALUES (?, ?, ?, ?)", data)

    #
    # Respond to graph modifications by flagging the necessary _check_* fields.
    #

    def detach(self):
        """Detach this step from the graph, but keep it in the database."""
        super().detach()
        self._flag_checks_with_products()
        # Source steps of the detached subtree lost a sink, so their metadata is stale.
        self.db.execute(RECURSIVE_CHECK_AFTER_SOURCES, (self.i,))

    def reattach(self, new_creator: Node):
        """Reattach the node to a new creator node, preserving its properties."""
        super().reattach(new_creator)
        self._flag_checks_with_products()

    def _flag_checks_with_products(self):
        """Flag the `_check_safe` and `_check_after` fields of this step and its products."""
        self.db.execute(RECURSIVE_CHECK_WITH_PRODUCTS, (self.i,))
