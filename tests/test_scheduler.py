# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Unit tests for stepup.core.scheduler."""

import time

import pytest

from stepup.core.enums import FileState, Need, StepState
from stepup.core.file import FILE_SCHEMA
from stepup.core.hash import FileHash, StepHash
from stepup.core.job import RunJob
from stepup.core.path import dir_range_upper
from stepup.core.scheduler import (
    APPLY_SAFE_UPDATE,
    EMPTY_CHANGED_AFTER,
    EMPTY_CHECK_AFTER,
    EMPTY_SAFE_UPDATE,
    INIT_CHANGED_AFTER,
    INIT_CHECK_AFTER,
    INIT_SAFE_UPDATE,
    PROPAGATE_UPDATE_CHECK_AFTER,
    PRUNE_DETACHED_CHECK_AFTER,
    RECOMPUTE_READY,
    SELECT_INPUTS,
    SELECT_NEXT_STEP,
    SELECT_RESOURCE_COUNTS,
    SELECT_SAFE_UPDATE,
    UPDATE_CHECK_AFTER,
    Scheduler,
)
from stepup.core.sqlite3 import connect
from stepup.core.step import STEP_SCHEMA, Step, unavailable_input_sql
from stepup.core.trellis import TRELLIS_SCHEMA
from stepup.core.workflow import RECONCILE_TARGET_DIRS, Workflow

# Shared verbatim with the (deleted) scheduler.UNAVAILABLE_INPUT: correlated on the outer
# node.i, see `unavailable_input_sql` (step.py) for the shared subquery body.
UNAVAILABLE_INPUT = unavailable_input_sql("node.i")


@pytest.fixture
def con():
    """In-memory SQLite connection with trellis + step + file schemas and a root node."""
    c = connect(":memory:")
    c.executescript(TRELLIS_SCHEMA.format(application_id=0, schema_version=0))
    # FILE_SCHEMA must load before STEP_SCHEMA: STEP_SCHEMA's step_file_check_ready_*
    # triggers are declared ON file, which requires the file table to already exist.
    c.executescript(FILE_SCHEMA)
    c.executescript(STEP_SCHEMA)
    # available_resource, target_path and target_dir are normally temp tables created by
    # Scheduler.initialize.
    c.execute(
        "CREATE TEMPORARY TABLE IF NOT EXISTS available_resource"
        " (name TEXT PRIMARY KEY, units INTEGER NOT NULL)"
    )
    c.execute("CREATE TEMPORARY TABLE IF NOT EXISTS target_path (path TEXT PRIMARY KEY)")
    c.execute(
        "CREATE TEMPORARY TABLE IF NOT EXISTS target_dir "
        "(path TEXT PRIMARY KEY, upper TEXT NOT NULL)"
    )
    # Root node has a self-referential creator.
    c.execute("INSERT INTO node (i, kind, label, creator, detached) VALUES (1, 'root', '', 1, 0)")
    return c


def _insert_step(
    con,
    node_id,
    creator_id,
    state,
    *,
    safe=False,
    safe_ignoring_hold=None,
    check_safe=False,
    holding=False,
    need=Need.DEFAULT,
    implied_need=None,
    duration=1.0,
    tail_time=1.0,
    check_after=False,
    detached=False,
    ready=True,
):
    """Insert a node row and a step row for a fictitious step.

    `ready` controls the new step._ready/_check_ready columns. By default (`True`) the
    step is inserted immediately dispatchable (`_ready=1, _check_ready=0`), matching the
    old live-EXISTS query's behavior for steps with no blocking inputs -- the common case
    for tests that don't exercise input-availability logic. Pass `ready=False` for tests
    that set up dependencies/input files and want to exercise the real readiness
    computation: after wiring up the inputs, call `_recompute_ready(con)` before querying
    SELECT_NEXT_STEP.

    `safe_ignoring_hold` defaults to `safe`, mirroring `Step.initialize()`'s seeding of
    `_safe_ignoring_hold` from the same `safe` value; pass it explicitly to set up a step
    that is only unsafe because of an active hold
    (`safe=False, safe_ignoring_hold=True`).
    """
    if implied_need is None:
        implied_need = need
    if safe_ignoring_hold is None:
        safe_ignoring_hold = safe
    con.execute(
        "INSERT INTO node (i, kind, label, creator, detached) VALUES (?, 'step', ?, ?, ?)",
        (node_id, f"echo {node_id}", creator_id, detached),
    )
    con.execute(
        "INSERT INTO step"
        " (node, state, need, duration, postpone_count,"
        " subshell, _safe, _check_safe, _safe_ignoring_hold, _holding, _implied_need,"
        " _tail_time, _check_after, _ready, _check_ready)"
        " VALUES (?, ?, ?, ?, 0, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            node_id,
            state.value,
            need.value,
            duration,
            safe,
            check_safe,
            safe_ignoring_hold,
            holding,
            implied_need.value,
            tail_time,
            check_after,
            ready,
            not ready,
        ),
    )


def _recompute_ready(con):
    """Run RECOMPUTE_READY directly, mirroring Scheduler._update_meta_inputs()."""
    con.execute(RECOMPUTE_READY)


def _insert_file(con, node_id, creator_id):
    """Insert an intermediate file-type node used to route dependencies between steps.

    No row in the file table is needed: the _check_after queries only use the node and
    dependency tables.
    """
    con.execute(
        "INSERT INTO node (i, kind, label, creator, detached) VALUES (?, 'file', ?, ?, 0)",
        (node_id, f"file_{node_id}.txt", creator_id),
    )


def _add_dep(con, source_id, sink_id):
    """Add a directed dependency edge from source to sink."""
    con.execute(
        "INSERT INTO dependency (source, sink) VALUES (?, ?)",
        (source_id, sink_id),
    )


def _insert_input_file(con, node_id, creator_id, state, *, detached=False, label=None):
    """Insert a file node + file table row for use as a step input.

    `label` defaults to `file_{node_id}.txt`; pass an explicit value for directory-target
    tests, which need labels with a specific path structure.
    """
    if label is None:
        label = f"file_{node_id}.txt"
    con.execute(
        "INSERT INTO node (i, kind, label, creator, detached) VALUES (?, 'file', ?, ?, ?)",
        (node_id, label, creator_id, detached),
    )
    if state in (FileState.MISSING, FileState.AWAITED, FileState.VOLATILE):
        hash_value = None
    else:  # STATIC, BUILT, OUTDATED
        hash_value = FileHash(b"\x01\x02\x03", 0o100644, 1000.0, 100, 42).to_json()
    con.execute(
        "INSERT INTO file (node, state, hash) VALUES (?, ?, ?)",
        (node_id, state.value, hash_value),
    )


def _add_dep_returning_id(con, source_id, sink_id):
    """Add a dependency edge and return its primary-key id."""
    cur = con.execute(
        "INSERT INTO dependency (source, sink) VALUES (?, ?)",
        (source_id, sink_id),
    )
    return cur.lastrowid


def _mark_dep_amended(con, dep_id):
    """Mark an existing dependency as amended."""
    con.execute("INSERT INTO amended_dep (i) VALUES (?)", (dep_id,))


def _get_runnable_ids(con, need_threshold=Need.OPTIONAL):
    """Run SELECT_NEXT_STEP and return the ids of results
    dispatched via the runnable (non-checkable) path.

    Every scenario using this helper inserts at most one PENDING step,
    so filtering SELECT_NEXT_STEP's (at most one) result by NOT has_hash
    is equivalent to running the old standalone SELECT_RUNNABLE_STEPS query.
    A test with multiple simultaneously-eligible PENDING steps
    should query SELECT_NEXT_STEP directly instead of relying on this helper,
    since SELECT_NEXT_STEP's LIMIT 1 only ever returns
    the single highest-priority candidate overall (checkable steps always win),
    not the best candidate per path.

    `need_threshold` mirrors `Workflow.need_threshold`: `OPTIONAL` (the default)
    reproduces pre-targeting behavior, since `_implied_need > OPTIONAL` is already
    implied by `STEP_DISPATCH_WHERE`.
    """
    rows = con.execute(SELECT_NEXT_STEP, (need_threshold.value,)).fetchall()
    return [row[0] for row in rows if not row[2]]


def _get_safe(con):
    """Return a dict mapping step node id -> _safe value."""
    return dict(con.execute("SELECT node, _safe FROM step").fetchall())


def _get_safe_ignoring_hold(con):
    """Return a dict mapping step node id -> _safe_ignoring_hold value."""
    return dict(con.execute("SELECT node, _safe_ignoring_hold FROM step").fetchall())


def _run_update_meta_safe(con):
    """Run the full update_meta_safe logic against a bare SQLite connection."""
    con.execute(INIT_SAFE_UPDATE)
    con.execute(EMPTY_SAFE_UPDATE)
    con.execute(SELECT_SAFE_UPDATE)
    con.execute(APPLY_SAFE_UPDATE)


def _run_update_meta_after(con):
    """Run the full update_meta_after logic against a bare SQLite connection.

    For target elevation, the caller must have populated the `target_path` temp table
    (see `_insert_target_path`) beforehand; with an empty table, no step is elevated.
    """
    con.execute(INIT_CHECK_AFTER)
    con.execute(INIT_CHANGED_AFTER)
    con.execute(EMPTY_CHECK_AFTER)
    con.execute(PRUNE_DETACHED_CHECK_AFTER)
    ncheck = con.execute("SELECT COUNT(*) FROM check_after").fetchone()[0]
    first = True
    while ncheck > 0:
        cur = con.execute(UPDATE_CHECK_AFTER, {"first": first})
        changed_ids = cur.fetchall()
        con.execute(EMPTY_CHECK_AFTER)
        con.execute(EMPTY_CHANGED_AFTER)
        con.executemany("INSERT INTO changed_after(i) VALUES (?)", changed_ids)
        cur = con.execute(PROPAGATE_UPDATE_CHECK_AFTER)
        ncheck = cur.rowcount
        first = False
    con.execute("UPDATE step SET _check_after = 0 WHERE _check_after = 1")


def _insert_target_path(con, *paths):
    """Populate the target_path temp table, mirroring Scheduler.initialize()."""
    con.executemany("INSERT INTO target_path VALUES (?)", ((path,) for path in paths))


def _insert_target_dir(con, *paths):
    """Populate the target_dir temp table, mirroring Scheduler.initialize()."""
    con.executemany(
        "INSERT INTO target_dir VALUES (?, ?)",
        ((path, dir_range_upper(path)) for path in paths),
    )


# -----------------------------------------------------------------------
# Tests for INIT/EMPTY/SELECT/APPLY_SAFE_UPDATE (_update_meta_safe)
# -----------------------------------------------------------------------


def test_running_creator_makes_product_safe(con):
    """Product of a RUNNING step gets _safe=1 after the update."""
    _insert_step(con, 2, 1, StepState.RUNNING, check_safe=True)
    _insert_step(con, 3, 2, StepState.PENDING)
    _run_update_meta_safe(con)
    assert _get_safe(con)[3] == 1


def test_succeeded_creator_makes_product_safe(con):
    """Product of a SUCCEEDED step gets _safe=1 after the update."""
    _insert_step(con, 2, 1, StepState.SUCCEEDED, check_safe=True)
    _insert_step(con, 3, 2, StepState.PENDING)
    _run_update_meta_safe(con)
    assert _get_safe(con)[3] == 1


def test_failed_creator_keeps_product_unsafe(con):
    """Product of a FAILED step keeps _safe=0 after the update."""
    _insert_step(con, 2, 1, StepState.FAILED, check_safe=True)
    _insert_step(con, 3, 2, StepState.PENDING)
    _run_update_meta_safe(con)
    assert _get_safe(con)[3] == 0


def test_pending_creator_keeps_product_unsafe(con):
    """Product of a PENDING step keeps _safe=0 after the update."""
    _insert_step(con, 2, 1, StepState.PENDING, check_safe=True)
    _insert_step(con, 3, 2, StepState.PENDING)
    _run_update_meta_safe(con)
    assert _get_safe(con)[3] == 0


def test_no_check_safe_skips_update(con):
    """When no step has _check_safe=1, no _safe values are updated."""
    _insert_step(con, 2, 1, StepState.RUNNING, check_safe=False)
    _insert_step(con, 3, 2, StepState.PENDING, check_safe=False)
    _run_update_meta_safe(con)
    assert _get_safe(con)[3] == 0


def test_deep_chain_propagates_safe(con):
    """Safety propagates through: root -> A(RUNNING) -> B(RUNNING) -> C(PENDING)."""
    _insert_step(con, 2, 1, StepState.RUNNING, check_safe=True)
    _insert_step(con, 3, 2, StepState.RUNNING)
    _insert_step(con, 4, 3, StepState.PENDING)
    _run_update_meta_safe(con)
    safe = _get_safe(con)
    assert safe[3] == 1  # B is safe: creator A is RUNNING
    assert safe[4] == 1  # C is safe: creator B is RUNNING


def test_failed_intermediate_blocks_grandchild(con):
    """RUNNING A -> FAILED B -> PENDING C: B._safe=1 (can run) but C._safe=0 (blocked)."""
    _insert_step(con, 2, 1, StepState.RUNNING, check_safe=True)
    _insert_step(con, 3, 2, StepState.FAILED)
    _insert_step(con, 4, 3, StepState.PENDING)
    _run_update_meta_safe(con)
    safe = _get_safe(con)
    assert safe[3] == 1  # B can still be queued: creator A is RUNNING
    assert safe[4] == 0  # C is blocked: creator B has not succeeded


def test_previously_safe_step_becomes_unsafe(con):
    """A step that was _safe=1 gets reset to 0 when its creator transitions to FAILED."""
    _insert_step(con, 2, 1, StepState.FAILED, check_safe=True, safe=True)
    _insert_step(con, 3, 2, StepState.PENDING, safe=True)
    _run_update_meta_safe(con)
    assert _get_safe(con)[3] == 0


def test_no_check_safe_leaves_safe_update_empty(con):
    """When no step has _check_safe=1, SELECT_SAFE_UPDATE inserts no rows."""
    _insert_step(con, 2, 1, StepState.RUNNING, check_safe=False)
    con.execute(INIT_SAFE_UPDATE)
    con.execute(EMPTY_SAFE_UPDATE)
    con.execute(SELECT_SAFE_UPDATE)
    assert con.execute("SELECT COUNT(*) FROM safe_update").fetchone()[0] == 0


def test_double_flagged_ancestor_chain_computes_correct_safe(con):
    """A grandchild reachable via two simultaneously-flagged ancestors gets the correct value.

    Reproduces the shape Step.detach()/recycle() creates via RECURSIVE_CHECK_WITH_PRODUCTS,
    which flags _check_safe on a step and all its recursive products at once: S(FAILED,
    check_safe) -> C(RUNNING, check_safe, creator=S) -> P(RUNNING, creator=C). C's own state
    would naively make it look like a safe creator, but its real creator S has failed, so P
    must end up unsafe. The old single-statement RECURSIVE_UPDATE_SAFE query got this wrong
    (produced _safe=1 for P); MIN(safe) aggregation in SELECT_SAFE_UPDATE fixes it.
    """
    _insert_step(con, 2, 1, StepState.FAILED, check_safe=True)
    _insert_step(con, 3, 2, StepState.RUNNING, check_safe=True)
    _insert_step(con, 4, 3, StepState.RUNNING)
    _run_update_meta_safe(con)
    safe = _get_safe(con)
    assert safe[3] == 0  # C is unsafe: its real creator S failed
    assert safe[4] == 0  # P is unsafe: the old query incorrectly produced 1 here


def test_holding_creator_keeps_child_unsafe(con):
    """A child of a RUNNING but holding creator stays unsafe, unlike the non-holding case."""
    _insert_step(con, 2, 1, StepState.RUNNING, check_safe=True, holding=True)
    _insert_step(con, 3, 2, StepState.PENDING)
    _run_update_meta_safe(con)
    assert _get_safe(con)[3] == 0


def test_holding_counter_above_one_keeps_child_unsafe(con):
    """A `_holding` counter above 1 (nested `hold()` calls) still keeps children unsafe,
    confirming SELECT_SAFE_UPDATE's `_holding = 0` check, not a boolean truthiness check.
    """
    _insert_step(con, 2, 1, StepState.RUNNING, check_safe=True, holding=2)
    _insert_step(con, 3, 2, StepState.PENDING)
    _run_update_meta_safe(con)
    assert _get_safe(con)[3] == 0


def test_release_makes_previously_held_child_safe(con):
    """A child of a RUNNING creator becomes safe once the creator stops holding."""
    _insert_step(con, 2, 1, StepState.RUNNING, check_safe=True, holding=False)
    _insert_step(con, 3, 2, StepState.PENDING)
    _run_update_meta_safe(con)
    assert _get_safe(con)[3] == 1


def test_holding_grandchild_blocks_only_its_own_children(con):
    """Holding only affects the holding step's own children, not itself nor siblings.

    Chain: root -> A(RUNNING) -> B(RUNNING, holding) -> C(PENDING). B itself is safe (its
    creator A is RUNNING and not holding), but C is unsafe because its creator B is holding.
    """
    _insert_step(con, 2, 1, StepState.RUNNING, check_safe=True)
    _insert_step(con, 3, 2, StepState.RUNNING, holding=True)
    _insert_step(con, 4, 3, StepState.PENDING)
    _run_update_meta_safe(con)
    safe = _get_safe(con)
    assert safe[3] == 1  # B is safe: creator A is RUNNING and not holding
    assert safe[4] == 0  # C is unsafe: its creator B is holding


def test_grandchild_safe_again_after_grandparent_stops_holding(con):
    """Once an ancestor's `_holding` clears, a previously blocked grandchild becomes safe."""
    _insert_step(con, 2, 1, StepState.RUNNING, check_safe=True)
    _insert_step(con, 3, 2, StepState.RUNNING, holding=False)
    _insert_step(con, 4, 3, StepState.PENDING)
    _run_update_meta_safe(con)
    safe = _get_safe(con)
    assert safe[3] == 1
    assert safe[4] == 1


def test_holding_creator_keeps_child_safe_ignoring_hold(con):
    """A child of a RUNNING but holding creator is _safe=0 but _safe_ignoring_hold=1.

    Same setup as `test_holding_creator_keeps_child_unsafe`, but also checks the "no hold"
    twin computed by the same SELECT_SAFE_UPDATE pass: the only thing that makes the child
    unsafe here is the hold, so ignoring it must flip the answer back to safe.
    """
    _insert_step(con, 2, 1, StepState.RUNNING, check_safe=True, holding=True)
    _insert_step(con, 3, 2, StepState.PENDING)
    _run_update_meta_safe(con)
    assert _get_safe(con)[3] == 0
    assert _get_safe_ignoring_hold(con)[3] == 1


def test_failed_creator_keeps_child_unsafe_ignoring_hold_too(con):
    """A child of a FAILED (not holding) creator is unsafe both with and without hold.

    Confirms _safe_ignoring_hold is not simply "always safe": it still respects the
    ordinary RUNNING/SUCCEEDED ancestor requirement, just not _holding.
    """
    _insert_step(con, 2, 1, StepState.FAILED, check_safe=True)
    _insert_step(con, 3, 2, StepState.PENDING)
    _run_update_meta_safe(con)
    assert _get_safe(con)[3] == 0
    assert _get_safe_ignoring_hold(con)[3] == 0


def test_holding_grandchild_safe_ignoring_hold_propagates(con):
    """_safe_ignoring_hold propagates down a chain exactly like _safe, minus the hold term.

    Chain: root -> A(RUNNING) -> B(RUNNING, holding) -> C(PENDING). C is unsafe (its
    creator B is holding) but safe-ignoring-hold, since both A and B are otherwise
    RUNNING/not-failed.
    """
    _insert_step(con, 2, 1, StepState.RUNNING, check_safe=True)
    _insert_step(con, 3, 2, StepState.RUNNING, holding=True)
    _insert_step(con, 4, 3, StepState.PENDING)
    _run_update_meta_safe(con)
    safe = _get_safe(con)
    safe_nh = _get_safe_ignoring_hold(con)
    assert safe[4] == 0
    assert safe_nh[4] == 1


# -----------------------------------------------------------------------
# Tests for update_meta_after (INIT/SELECT/APPLY_UPDATE_CHECK_AFTER)
#
# Dependencies follow the two-hop pattern:  step_A -> file -> step_B
# meaning: dep(source=step_A, sink=file) + dep(source=file, sink=step_B).
# The queries compute _tail_time and _implied_need by traversing these two-hop paths.
# -----------------------------------------------------------------------


def test_isolated_step_tail_time_set_to_duration(con):
    """A step with no file sinks gets _tail_time = duration after the update."""
    _insert_step(con, 2, 1, StepState.PENDING, check_after=True, duration=3.5, tail_time=0.0)
    _run_update_meta_after(con)
    row = con.execute("SELECT _tail_time, _check_after FROM step WHERE node = 2").fetchone()
    assert row[0] == pytest.approx(3.5)
    assert row[1] == 0  # _check_after cleared


def test_two_step_chain_upstream_tail_time_includes_downstream(con):
    """step_A._tail_time = A.duration + B._tail_time when A -> file -> B."""
    # B._tail_time is pre-set to its duration; only A has check_after=True.
    _insert_step(con, 2, 1, StepState.PENDING, check_after=True, duration=1.0, tail_time=0.0)
    _insert_file(con, 3, 1)
    _insert_step(con, 4, 1, StepState.PENDING, duration=2.0, tail_time=2.0)
    _add_dep(con, 2, 3)  # A -> file
    _add_dep(con, 3, 4)  # file -> B
    _run_update_meta_after(con)
    row = con.execute("SELECT _tail_time FROM step WHERE node = 2").fetchone()
    assert row[0] == pytest.approx(3.0)  # A.duration + B._tail_time = 1.0 + 2.0


def test_three_step_chain_processes_bottom_up(con):
    """When A and B both have check_after=True in A -> F1 -> B -> F2 -> C, A is deferred.

    INIT_CHECK_AFTER removes A from the initial set because B (also check_after=True) is a
    downstream sink of A.  B is processed first; propagation from B then queues A,
    so A._tail_time picks up B's already-updated value.
    """
    _insert_step(con, 2, 1, StepState.PENDING, check_after=True, duration=1.0, tail_time=0.0)
    _insert_file(con, 3, 1)
    _insert_step(con, 4, 1, StepState.PENDING, check_after=True, duration=2.0, tail_time=0.0)
    _insert_file(con, 5, 1)
    _insert_step(con, 6, 1, StepState.PENDING, duration=3.0, tail_time=3.0)
    _add_dep(con, 2, 3)
    _add_dep(con, 3, 4)
    _add_dep(con, 4, 5)
    _add_dep(con, 5, 6)
    _run_update_meta_after(con)
    tail = dict(con.execute("SELECT node, _tail_time FROM step").fetchall())
    assert tail[4] == pytest.approx(5.0)  # B = B.duration + C._tail_time = 2.0 + 3.0
    assert tail[2] == pytest.approx(6.0)  # A = A.duration + B._tail_time = 1.0 + 5.0


def test_implied_need_propagates_from_sink(con):
    """A step's _implied_need is raised to the maximum _implied_need of its sinks."""
    _insert_step(
        con,
        2,
        1,
        StepState.PENDING,
        check_after=True,
        need=Need.DEFAULT,
        implied_need=Need.DEFAULT,
    )
    _insert_file(con, 3, 1)
    _insert_step(con, 4, 1, StepState.PENDING, need=Need.PLAN, implied_need=Need.PLAN)
    _add_dep(con, 2, 3)
    _add_dep(con, 3, 4)
    _run_update_meta_after(con)
    row = con.execute("SELECT _implied_need FROM step WHERE node = 2").fetchone()
    assert row[0] == Need.PLAN.value


def test_tail_time_is_maximum_over_parallel_sinks(con):
    """When a step supplies to two independent sinks, _tail_time tracks the longer branch."""
    _insert_step(con, 2, 1, StepState.PENDING, check_after=True, duration=1.0, tail_time=0.0)
    _insert_file(con, 3, 1)  # intermediate for B
    _insert_file(con, 4, 1)  # intermediate for C
    _insert_step(con, 5, 1, StepState.PENDING, duration=2.0, tail_time=2.0)  # B (shorter)
    _insert_step(con, 6, 1, StepState.PENDING, duration=5.0, tail_time=5.0)  # C (longer)
    _add_dep(con, 2, 3)
    _add_dep(con, 3, 5)
    _add_dep(con, 2, 4)
    _add_dep(con, 4, 6)
    _run_update_meta_after(con)
    row = con.execute("SELECT _tail_time FROM step WHERE node = 2").fetchone()
    assert row[0] == pytest.approx(6.0)  # A.duration + max(B._tail_time, C._tail_time) = 1 + 5


# -----------------------------------------------------------------------
# Tests for target elevation in UPDATE_CHECK_AFTER
# -----------------------------------------------------------------------


def test_target_match_elevates_producer(con):
    """A step whose output file matches a target is elevated to _implied_need=TARGET."""
    _insert_step(
        con, 2, 1, StepState.PENDING, check_after=True, need=Need.DEFAULT, implied_need=Need.DEFAULT
    )
    _insert_input_file(con, 3, 2, FileState.AWAITED)  # step 2's output
    _add_dep(con, 2, 3)
    _insert_target_path(con, "file_3.txt")
    _run_update_meta_after(con)
    row = con.execute("SELECT need, _implied_need FROM step WHERE node = 2").fetchone()
    assert row[0] == Need.DEFAULT.value  # need itself is never written
    assert row[1] == Need.TARGET.value


def test_target_match_on_volatile_sink_not_elevated(con):
    """A step whose only target-matching sink is VOLATILE is not elevated.

    Dependency sinks of a step are exactly its out_paths (AWAITED/BUILT/OUTDATED) and
    vol_paths (VOLATILE); the ofile.state != VOLATILE filter keeps elevation restricted to
    regular outputs, matching the fact that a build target can never legitimately be a
    vol_path (declaration-time checks reject that combination for reachable graphs).
    """
    _insert_step(
        con,
        2,
        1,
        StepState.PENDING,
        check_after=True,
        need=Need.OPTIONAL,
        implied_need=Need.OPTIONAL,
    )
    _insert_input_file(con, 3, 2, FileState.VOLATILE)
    _add_dep(con, 2, 3)
    _insert_target_path(con, "file_3.txt")
    _run_update_meta_after(con)
    row = con.execute("SELECT _implied_need FROM step WHERE node = 2").fetchone()
    assert row[0] == Need.OPTIONAL.value


def test_target_match_on_detached_sink_not_elevated(con):
    """A step whose only target-matching sink is detached is not elevated."""
    _insert_step(
        con,
        2,
        1,
        StepState.PENDING,
        check_after=True,
        need=Need.OPTIONAL,
        implied_need=Need.OPTIONAL,
    )
    _insert_input_file(con, 3, 2, FileState.AWAITED, detached=True)
    _add_dep(con, 2, 3)
    _insert_target_path(con, "file_3.txt")
    _run_update_meta_after(con)
    row = con.execute("SELECT _implied_need FROM step WHERE node = 2").fetchone()
    assert row[0] == Need.OPTIONAL.value


def test_target_match_keeps_plan_need(con):
    """A PLAN-need step producing a target keeps _implied_need=PLAN (scalar MAX)."""
    _insert_step(
        con, 2, 1, StepState.PENDING, check_after=True, need=Need.PLAN, implied_need=Need.PLAN
    )
    _insert_input_file(con, 3, 2, FileState.AWAITED)
    _add_dep(con, 2, 3)
    _insert_target_path(con, "file_3.txt")
    _run_update_meta_after(con)
    row = con.execute("SELECT _implied_need FROM step WHERE node = 2").fetchone()
    assert row[0] == Need.PLAN.value


def test_target_match_elevates_optional_need_step(con):
    """An OPTIONAL-need step whose output is a target is elevated to TARGET and dispatched."""
    _insert_step(
        con,
        2,
        1,
        StepState.PENDING,
        safe=True,
        check_after=True,
        need=Need.OPTIONAL,
        implied_need=Need.OPTIONAL,
    )
    _insert_input_file(con, 3, 2, FileState.AWAITED)
    _add_dep(con, 2, 3)
    _insert_target_path(con, "file_3.txt")
    _run_update_meta_after(con)
    row = con.execute("SELECT _implied_need FROM step WHERE node = 2").fetchone()
    assert row[0] == Need.TARGET.value
    assert _get_runnable_ids(con, Need.DEFAULT) == [2]


def test_target_match_propagates_to_upstream_source(con):
    """Elevating a target-producing step propagates TARGET to its (transitive) sources.

    Chain: step 2 -> file 3 -> step 4 -> file 5 (the target). Only step 4 is initially
    check_after-flagged (mirroring Workflow.reconcile_targets(), which flags exactly the
    target's producer); step 2 is expected to be picked up by
    PROPAGATE_UPDATE_CHECK_AFTER once step 4's _implied_need actually changes.
    """
    _insert_step(
        con,
        2,
        1,
        StepState.PENDING,
        check_after=False,
        need=Need.DEFAULT,
        implied_need=Need.DEFAULT,
    )
    _insert_file(con, 3, 1)
    _insert_step(
        con, 4, 1, StepState.PENDING, check_after=True, need=Need.DEFAULT, implied_need=Need.DEFAULT
    )
    _insert_input_file(con, 5, 4, FileState.AWAITED)
    _add_dep(con, 2, 3)
    _add_dep(con, 3, 4)
    _add_dep(con, 4, 5)
    _insert_target_path(con, "file_5.txt")
    _run_update_meta_after(con)
    rows = dict(con.execute("SELECT node, _implied_need FROM step").fetchall())
    assert rows[4] == Need.TARGET.value
    assert rows[2] == Need.TARGET.value


def test_stale_target_implied_need_is_demoted(con):
    """A step with a stale _implied_need=TARGET is recomputed down when no target matches.

    Simulates Workflow.reconcile_targets() flagging a step left over (_check_after=1) from
    a previous run with a different target set: recomputation is state-free, so it settles
    back to MAX(need, sink-derived) without needing to know it was ever TARGET.
    """
    _insert_step(
        con, 2, 1, StepState.PENDING, check_after=True, need=Need.DEFAULT, implied_need=Need.TARGET
    )
    _run_update_meta_after(con)
    row = con.execute("SELECT _implied_need FROM step WHERE node = 2").fetchone()
    assert row[0] == Need.DEFAULT.value


# -----------------------------------------------------------------------
# Tests for directory-target elevation in UPDATE_CHECK_AFTER
# -----------------------------------------------------------------------


def test_dir_target_elevates_producer_of_file_inside(con):
    """A DEFAULT-need step whose output falls under a directory target is elevated."""
    _insert_step(
        con, 2, 1, StepState.PENDING, check_after=True, need=Need.DEFAULT, implied_need=Need.DEFAULT
    )
    _insert_input_file(con, 3, 2, FileState.AWAITED, label="out/report/fig.svg")
    _add_dep(con, 2, 3)
    _insert_target_dir(con, "out/report/")
    _run_update_meta_after(con)
    row = con.execute("SELECT _implied_need FROM step WHERE node = 2").fetchone()
    assert row[0] == Need.TARGET.value


def test_dir_target_boundary_sibling_not_matched(con):
    """A file sharing the directory target's prefix but not its slash boundary is not matched.

    `out/report_debug.txt` starts with the string `out/report` but not with `out/report/`,
    so it must fall outside the [path, upper) range.
    """
    _insert_step(
        con, 2, 1, StepState.PENDING, check_after=True, need=Need.DEFAULT, implied_need=Need.DEFAULT
    )
    _insert_input_file(con, 3, 2, FileState.AWAITED, label="out/report_debug.txt")
    _add_dep(con, 2, 3)
    _insert_target_dir(con, "out/report/")
    _run_update_meta_after(con)
    row = con.execute("SELECT _implied_need FROM step WHERE node = 2").fetchone()
    assert row[0] == Need.DEFAULT.value


def test_dir_target_matches_directory_node_itself(con):
    """The directory `File` node itself (label equal to the target) is matched.

    `'out/report/' >= 'out/report/'` holds, so a step that produces the directory node
    (e.g. via `mkdir`) is elevated too.
    """
    _insert_step(
        con, 2, 1, StepState.PENDING, check_after=True, need=Need.DEFAULT, implied_need=Need.DEFAULT
    )
    _insert_input_file(con, 3, 2, FileState.AWAITED, label="out/report/")
    _add_dep(con, 2, 3)
    _insert_target_dir(con, "out/report/")
    _run_update_meta_after(con)
    row = con.execute("SELECT _implied_need FROM step WHERE node = 2").fetchone()
    assert row[0] == Need.TARGET.value


def test_dir_target_matches_nested_subdirectory(con):
    """A file in a nested subdirectory under the target directory is matched."""
    _insert_step(
        con, 2, 1, StepState.PENDING, check_after=True, need=Need.DEFAULT, implied_need=Need.DEFAULT
    )
    _insert_input_file(con, 3, 2, FileState.AWAITED, label="out/report/sub/fig.svg")
    _add_dep(con, 2, 3)
    _insert_target_dir(con, "out/report/")
    _run_update_meta_after(con)
    row = con.execute("SELECT _implied_need FROM step WHERE node = 2").fetchone()
    assert row[0] == Need.TARGET.value


def test_dir_target_matches_non_ascii_label(con):
    """A non-ASCII label under the target directory is matched (BINARY collation, UTF-8
    byte order preserves code-point order)."""
    _insert_step(
        con, 2, 1, StepState.PENDING, check_after=True, need=Need.DEFAULT, implied_need=Need.DEFAULT
    )
    _insert_input_file(con, 3, 2, FileState.AWAITED, label="out/report/fé中.svg")
    _add_dep(con, 2, 3)
    _insert_target_dir(con, "out/report/")
    _run_update_meta_after(con)
    row = con.execute("SELECT _implied_need FROM step WHERE node = 2").fetchone()
    assert row[0] == Need.TARGET.value


def test_dir_target_optional_producer_not_elevated(con):
    """An OPTIONAL-need step whose output is under a directory target is not elevated.

    Directory targets only reach steps whose declared `need` is DEFAULT; exact targets
    remain the only way to reach an OPTIONAL step.
    """
    _insert_step(
        con,
        2,
        1,
        StepState.PENDING,
        check_after=True,
        need=Need.OPTIONAL,
        implied_need=Need.OPTIONAL,
    )
    _insert_input_file(con, 3, 2, FileState.AWAITED, label="out/report/fig.svg")
    _add_dep(con, 2, 3)
    _insert_target_dir(con, "out/report/")
    _run_update_meta_after(con)
    row = con.execute("SELECT _implied_need FROM step WHERE node = 2").fetchone()
    assert row[0] == Need.OPTIONAL.value


def test_dir_target_volatile_output_not_elevated(con):
    """A step whose only in-directory sink is VOLATILE is not elevated."""
    _insert_step(
        con, 2, 1, StepState.PENDING, check_after=True, need=Need.DEFAULT, implied_need=Need.DEFAULT
    )
    _insert_input_file(con, 3, 2, FileState.VOLATILE, label="out/report/fig.svg")
    _add_dep(con, 2, 3)
    _insert_target_dir(con, "out/report/")
    _run_update_meta_after(con)
    row = con.execute("SELECT _implied_need FROM step WHERE node = 2").fetchone()
    assert row[0] == Need.DEFAULT.value


def test_dir_target_detached_sink_not_elevated(con):
    """A step whose only in-directory sink is detached is not elevated."""
    _insert_step(
        con, 2, 1, StepState.PENDING, check_after=True, need=Need.DEFAULT, implied_need=Need.DEFAULT
    )
    _insert_input_file(con, 3, 2, FileState.AWAITED, label="out/report/fig.svg", detached=True)
    _add_dep(con, 2, 3)
    _insert_target_dir(con, "out/report/")
    _run_update_meta_after(con)
    row = con.execute("SELECT _implied_need FROM step WHERE node = 2").fetchone()
    assert row[0] == Need.DEFAULT.value


def test_empty_target_dir_is_no_op(con):
    """With no directory targets, a DEFAULT-need step is not elevated."""
    _insert_step(
        con, 2, 1, StepState.PENDING, check_after=True, need=Need.DEFAULT, implied_need=Need.DEFAULT
    )
    _insert_input_file(con, 3, 2, FileState.AWAITED, label="out/report/fig.svg")
    _add_dep(con, 2, 3)
    _run_update_meta_after(con)
    row = con.execute("SELECT _implied_need FROM step WHERE node = 2").fetchone()
    assert row[0] == Need.DEFAULT.value


def test_dir_target_nested_and_duplicate_ranges_are_harmless(con):
    """Overlapping directory targets (a parent and a child, listed twice) still elevate once."""
    _insert_step(
        con, 2, 1, StepState.PENDING, check_after=True, need=Need.DEFAULT, implied_need=Need.DEFAULT
    )
    _insert_input_file(con, 3, 2, FileState.AWAITED, label="out/report/sub/fig.svg")
    _add_dep(con, 2, 3)
    _insert_target_dir(con, "out/", "out/report/", "out/report/sub/")
    _run_update_meta_after(con)
    row = con.execute("SELECT _implied_need FROM step WHERE node = 2").fetchone()
    assert row[0] == Need.TARGET.value


def test_dir_target_exact_arm_plan_unchanged(con):
    """The exact-path arm's plan is unchanged by the directory-target arm: it still probes
    `target_path` via the `IN`-operator index, and never falls back to a scan of it."""
    con.execute(INIT_CHECK_AFTER)
    plan = "\n".join(
        row[3] for row in con.execute(f"EXPLAIN QUERY PLAN {UPDATE_CHECK_AFTER}", {"first": True})
    )
    assert "USING INDEX sqlite_autoindex_target_path_1 FOR IN-OPERATOR" in plan
    assert "SCAN target_path" not in plan


def test_dir_target_arm_plan_probes_target_dir_index(con):
    """The directory-target arm probes `target_dir` via its PK index with a range (`path<?`),
    as a separate correlated subquery -- not via a label index on `onode`."""
    con.execute(INIT_CHECK_AFTER)
    plan = "\n".join(
        row[3] for row in con.execute(f"EXPLAIN QUERY PLAN {UPDATE_CHECK_AFTER}", {"first": True})
    )
    assert "target_dir" in plan
    assert "sqlite_autoindex_target_dir_1" in plan


def test_reconcile_target_dirs_plan_uses_range_scan(con):
    """`Workflow.RECONCILE_TARGET_DIRS`'s `CROSS JOIN` keeps the `node_kind_label`
    range scan (`kind=? AND label>? AND label<?`), guarding against a well-meaning cleanup
    that turns the `CROSS JOIN` into a plain `JOIN` -- which would degrade to a full scan of
    every file node (`kind=?` only, no label bounds).
    """
    plan = "\n".join(row[3] for row in con.execute(f"EXPLAIN QUERY PLAN {RECONCILE_TARGET_DIRS}"))
    assert "SCAN target_dir" in plan
    assert "kind=? AND label>? AND label<?" in plan


# -----------------------------------------------------------------------
# Tests for the need_threshold bound parameter (SELECT_NEXT_STEP)
# -----------------------------------------------------------------------


def test_default_implied_step_excluded_with_default_threshold(con):
    """With need_threshold=DEFAULT (targets set), a DEFAULT-implied step is not dispatched."""
    _insert_step(con, 2, 1, StepState.PENDING, safe=True, implied_need=Need.DEFAULT)
    assert _get_runnable_ids(con, Need.DEFAULT) == []
    assert _get_runnable_ids(con, Need.OPTIONAL) == [2]


def test_target_implied_step_included_with_default_threshold(con):
    """With need_threshold=DEFAULT, a TARGET-implied step is still dispatched."""
    _insert_step(con, 2, 1, StepState.PENDING, safe=True, implied_need=Need.TARGET)
    assert _get_runnable_ids(con, Need.DEFAULT) == [2]


def test_no_check_after_skips_update(con):
    """When no step has _check_after=1, no metadata values are updated."""
    _insert_step(con, 2, 1, StepState.PENDING, duration=3.0, tail_time=1.0, check_after=False)
    _run_update_meta_after(con)
    row = con.execute("SELECT _tail_time FROM step WHERE node = 2").fetchone()
    assert row[0] == pytest.approx(1.0)  # unchanged


def test_update_meta_after_then_dispatch_order_by_tail_time(con):
    """A derived (not manually pre-set) _tail_time still drives SELECT_NEXT_STEP's dispatch order.

    test_tail_time_is_maximum_over_parallel_sinks (above) checks the propagated value in
    isolation; test_ordering_higher_tail_time_first (below) checks SELECT_NEXT_STEP's ordering
    against manually pre-set _tail_time values. This chains the two: A and B both feed C, A's
    branch has the longer duration, and after _run_update_meta_after derives real _tail_time
    values for A and B, SELECT_NEXT_STEP must dispatch A (the longer critical path) first.
    """
    # A (node 2): longer branch.
    _insert_step(
        con, 2, 1, StepState.PENDING, safe=True, check_after=True, duration=5.0, tail_time=0.0
    )
    # B (node 4): shorter branch.
    _insert_step(
        con, 4, 1, StepState.PENDING, safe=True, check_after=True, duration=1.0, tail_time=0.0
    )
    _insert_file(con, 3, 1)  # intermediate for A
    _insert_file(con, 5, 1)  # intermediate for B
    # C (node 6): shared sink, already SUCCEEDED so it isn't itself a competing PENDING candidate.
    _insert_step(con, 6, 1, StepState.SUCCEEDED, duration=2.0, tail_time=2.0)
    _add_dep(con, 2, 3)  # A -> file
    _add_dep(con, 3, 6)  # file -> C
    _add_dep(con, 4, 5)  # B -> file
    _add_dep(con, 5, 6)  # file -> C

    _run_update_meta_after(con)
    tail = dict(con.execute("SELECT node, _tail_time FROM step").fetchall())
    assert tail[2] == pytest.approx(7.0)  # A.duration + C._tail_time = 5.0 + 2.0
    assert tail[4] == pytest.approx(3.0)  # B.duration + C._tail_time = 1.0 + 2.0

    # SELECT_NEXT_STEP carries a LIMIT 1, so only the top-priority candidate comes back;
    # A's higher derived _tail_time must win.
    ids = _get_runnable_ids(con)
    assert ids == [2]


def test_update_meta_after_elevates_flagged_step_two_hops_upstream(con):
    """Every flagged step is recomputed, also when a flagged sink is two hops downstream.

    Chain A -> B -> C, all three flagged. A is an OPTIONAL step whose `_implied_need` is
    stale (it was left at OPTIONAL while C was detached), while B and C already hold their
    final values. Recomputing only the most-downstream flagged step and relying on
    propagation to reach the rest does not work here: B does not change, so propagation
    stops before A. A must be elevated to DEFAULT all the same, otherwise it is never
    dispatched (`STEP_DISPATCH_WHERE` requires `_implied_need > OPTIONAL`) and B and C
    stay PENDING forever.
    """
    # A (node 2): declared OPTIONAL, with a stale implied need.
    _insert_step(con, 2, 1, StepState.PENDING, check_after=True, need=Need.OPTIONAL, tail_time=3.0)
    _insert_file(con, 3, 1)
    # B (node 4): unchanged, so it does not propagate anything by itself.
    _insert_step(con, 4, 1, StepState.PENDING, check_after=True, tail_time=2.0)
    _insert_file(con, 5, 1)
    # C (node 6): unchanged as well.
    _insert_step(con, 6, 1, StepState.PENDING, check_after=True, tail_time=1.0)
    _add_dep(con, 2, 3)  # A -> file
    _add_dep(con, 3, 4)  # file -> B
    _add_dep(con, 4, 5)  # B -> file
    _add_dep(con, 5, 6)  # file -> C

    _run_update_meta_after(con)
    implied = dict(con.execute("SELECT node, _implied_need FROM step").fetchall())
    assert implied[2] == Need.DEFAULT
    assert implied[4] == Need.DEFAULT
    assert implied[6] == Need.DEFAULT


# -----------------------------------------------------------------------
# Tests for PROPAGATE_UPDATE_CHECK_AFTER
# -----------------------------------------------------------------------


def test_propagate_sources_fork_no_duplicates(con):
    """Sources in fork pattern don't cause duplicate insertions.

    Fork pattern using deps: E -> file_e -> C, D
    When both C and D are in the changed-ids seed set, both depend on file_e which is
    supplied by E. PROPAGATE_UPDATE_CHECK_AFTER should insert E once, not twice.

    This is a regression test for: sqlite3.IntegrityError: UNIQUE constraint failed: check_after.i
    """
    # E (node 4) consumes file_f and supplies file_e
    _insert_step(con, 4, 1, StepState.PENDING)
    # file_e (node 5) is supplied by E
    _insert_file(con, 5, 1)
    # C (node 6) consumes file_e
    _insert_step(con, 6, 1, StepState.PENDING)
    # D (node 8) consumes file_e
    _insert_step(con, 8, 1, StepState.PENDING)

    # Two-hop dependencies: step -> file -> step
    _add_dep(con, 4, 5)  # E -> file_e
    _add_dep(con, 5, 6)  # file_e -> C
    _add_dep(con, 5, 8)  # file_e -> D

    # Create check_after and changed_after tables, and populate changed_after with C and D
    con.execute(INIT_CHECK_AFTER)
    con.execute(INIT_CHANGED_AFTER)
    con.executemany("INSERT INTO changed_after (i) VALUES (?)", [(6,), (8,)])

    # Run PROPAGATE_UPDATE_CHECK_AFTER - should not fail with UNIQUE constraint
    con.execute(PROPAGATE_UPDATE_CHECK_AFTER)

    # Verify E (node 4) is in check_after exactly once
    result = con.execute("SELECT COUNT(*) FROM check_after WHERE i = 4").fetchone()
    assert result[0] == 1

    # Verify only E is in check_after
    result = con.execute("SELECT COUNT(*) FROM check_after").fetchone()
    assert result[0] == 1


# -----------------------------------------------------------------------
# Tests for SELECT_NEXT_STEP's runnable (non-checkable) path
# -----------------------------------------------------------------------


def test_runnable_step_with_no_inputs(con):
    """A PENDING, safe, non-detached DEFAULT step with no file inputs is returned."""
    _insert_step(con, 2, 1, StepState.PENDING, safe=True, implied_need=Need.DEFAULT)
    assert _get_runnable_ids(con) == [2]


def test_running_step_not_runnable(con):
    """A RUNNING step is excluded — only PENDING steps are candidates."""
    _insert_step(con, 2, 1, StepState.RUNNING, safe=True, implied_need=Need.DEFAULT)
    assert _get_runnable_ids(con) == []


def test_checking_step_not_runnable(con):
    """A CHECKING step is excluded — only PENDING steps are candidates."""
    _insert_step(con, 2, 1, StepState.CHECKING, safe=True, implied_need=Need.DEFAULT)
    assert _get_runnable_ids(con) == []


def test_succeeded_step_not_runnable(con):
    """A SUCCEEDED step is excluded."""
    _insert_step(con, 2, 1, StepState.SUCCEEDED, safe=True, implied_need=Need.DEFAULT)
    assert _get_runnable_ids(con) == []


def test_failed_step_not_runnable(con):
    """A FAILED step is excluded."""
    _insert_step(con, 2, 1, StepState.FAILED, safe=True, implied_need=Need.DEFAULT)
    assert _get_runnable_ids(con) == []


def test_unsafe_step_not_runnable(con):
    """A PENDING step with _safe=0 is excluded."""
    _insert_step(con, 2, 1, StepState.PENDING, safe=False, implied_need=Need.DEFAULT)
    assert _get_runnable_ids(con) == []


def test_detached_step_not_runnable(con):
    """A detached PENDING step is excluded."""
    _insert_step(con, 2, 1, StepState.PENDING, safe=True, implied_need=Need.DEFAULT, detached=True)
    assert _get_runnable_ids(con) == []


def test_optional_step_not_runnable(con):
    """A PENDING step whose _implied_need is OPTIONAL is excluded."""
    _insert_step(con, 2, 1, StepState.PENDING, safe=True, implied_need=Need.OPTIONAL)
    assert _get_runnable_ids(con) == []


def test_postponed_step_not_runnable(con):
    """A PENDING step with postponed=1 is excluded."""
    _insert_step(con, 2, 1, StepState.PENDING, safe=True, implied_need=Need.DEFAULT)
    con.execute("UPDATE step SET postponed = 1 WHERE node = 2")
    assert _get_runnable_ids(con) == []


# -- input file blocking -------------------------------------------------


def test_volatile_input_blocks_step(con):
    """A VOLATILE input always blocks a step, regardless of initial/amended status."""
    _insert_step(con, 2, 1, StepState.PENDING, safe=True, implied_need=Need.DEFAULT, ready=False)
    _insert_input_file(con, 3, 1, FileState.VOLATILE)
    _add_dep(con, 3, 2)
    _recompute_ready(con)
    assert _get_runnable_ids(con) == []


def test_initial_input_awaited_blocks_step(con):
    """An initial dependency on an AWAITED file blocks the step."""
    _insert_step(con, 2, 1, StepState.PENDING, safe=True, implied_need=Need.DEFAULT, ready=False)
    _insert_input_file(con, 3, 1, FileState.AWAITED)
    _add_dep(con, 3, 2)
    _recompute_ready(con)
    assert _get_runnable_ids(con) == []


def test_initial_input_outdated_blocks_step(con):
    """An initial dependency on an OUTDATED file blocks the step."""
    _insert_step(con, 2, 1, StepState.PENDING, safe=True, implied_need=Need.DEFAULT, ready=False)
    _insert_input_file(con, 3, 1, FileState.OUTDATED)
    _add_dep(con, 3, 2)
    _recompute_ready(con)
    assert _get_runnable_ids(con) == []


def test_initial_input_missing_blocks_step(con):
    """An initial dependency on a MISSING file blocks the step."""
    _insert_step(con, 2, 1, StepState.PENDING, safe=True, implied_need=Need.DEFAULT, ready=False)
    _insert_input_file(con, 3, 1, FileState.MISSING)
    _add_dep(con, 3, 2)
    _recompute_ready(con)
    assert _get_runnable_ids(con) == []


def test_initial_input_detached_node_blocks_step(con):
    """An initial dependency on a detached file node blocks the step, regardless of file state."""
    _insert_step(con, 2, 1, StepState.PENDING, safe=True, implied_need=Need.DEFAULT, ready=False)
    _insert_input_file(con, 3, 1, FileState.BUILT, detached=True)
    _add_dep(con, 3, 2)
    _recompute_ready(con)
    assert _get_runnable_ids(con) == []


def test_initial_input_built_allows_step(con):
    """An initial dependency on a BUILT file does not block the step."""
    _insert_step(con, 2, 1, StepState.PENDING, safe=True, implied_need=Need.DEFAULT, ready=False)
    _insert_input_file(con, 3, 1, FileState.BUILT)
    _add_dep(con, 3, 2)
    _recompute_ready(con)
    assert _get_runnable_ids(con) == [2]


def test_initial_input_static_allows_step(con):
    """An initial dependency on a STATIC file does not block the step."""
    _insert_step(con, 2, 1, StepState.PENDING, safe=True, implied_need=Need.DEFAULT, ready=False)
    _insert_input_file(con, 3, 1, FileState.STATIC)
    _add_dep(con, 3, 2)
    _recompute_ready(con)
    assert _get_runnable_ids(con) == [2]


def test_amended_input_awaited_blocks_step(con):
    """An amended, attached input in AWAITED state blocks the step."""
    _insert_step(con, 2, 1, StepState.PENDING, safe=True, implied_need=Need.DEFAULT, ready=False)
    _insert_input_file(con, 3, 1, FileState.AWAITED)
    dep_id = _add_dep_returning_id(con, 3, 2)
    _mark_dep_amended(con, dep_id)
    _recompute_ready(con)
    assert _get_runnable_ids(con) == []


def test_amended_input_outdated_blocks_step(con):
    """An amended, attached input in OUTDATED state blocks the step."""
    _insert_step(con, 2, 1, StepState.PENDING, safe=True, implied_need=Need.DEFAULT, ready=False)
    _insert_input_file(con, 3, 1, FileState.OUTDATED)
    dep_id = _add_dep_returning_id(con, 3, 2)
    _mark_dep_amended(con, dep_id)
    _recompute_ready(con)
    assert _get_runnable_ids(con) == []


def test_amended_input_built_allows_step(con):
    """An amended, attached input in BUILT state does not block the step."""
    _insert_step(con, 2, 1, StepState.PENDING, safe=True, implied_need=Need.DEFAULT, ready=False)
    _insert_input_file(con, 3, 1, FileState.BUILT)
    dep_id = _add_dep_returning_id(con, 3, 2)
    _mark_dep_amended(con, dep_id)
    _recompute_ready(con)
    assert _get_runnable_ids(con) == [2]


def test_amended_input_missing_allows_step(con):
    """An amended, attached input in MISSING state does not block the step.

    MISSING is neither AWAITED nor OUTDATED, so case 1 of the blocking condition does not apply.
    The step proceeds and will validate its amended inputs before running.
    """
    _insert_step(con, 2, 1, StepState.PENDING, safe=True, implied_need=Need.DEFAULT, ready=False)
    _insert_input_file(con, 3, 1, FileState.MISSING)
    dep_id = _add_dep_returning_id(con, 3, 2)
    _mark_dep_amended(con, dep_id)
    _recompute_ready(con)
    assert _get_runnable_ids(con) == [2]


def test_amended_input_detached_allows_step(con):
    """An amended dependency on a detached file node does not block the step.

    Case 1 requires NOT input_node.detached, and case 2 only covers initial (non-amended) deps,
    so a detached amended input is not a blocking condition.
    """
    _insert_step(con, 2, 1, StepState.PENDING, safe=True, implied_need=Need.DEFAULT, ready=False)
    _insert_input_file(con, 3, 1, FileState.MISSING, detached=True)
    dep_id = _add_dep_returning_id(con, 3, 2)
    _mark_dep_amended(con, dep_id)
    _recompute_ready(con)
    assert _get_runnable_ids(con) == [2]


def test_one_ready_one_blocking_input_excludes_step(con):
    """When at least one input is blocking, the step is excluded even if others are ready."""
    _insert_step(con, 2, 1, StepState.PENDING, safe=True, implied_need=Need.DEFAULT, ready=False)
    _insert_input_file(con, 3, 1, FileState.BUILT)  # ready
    _insert_input_file(con, 4, 1, FileState.AWAITED)  # blocking
    _add_dep(con, 3, 2)
    _add_dep(con, 4, 2)
    _recompute_ready(con)
    assert _get_runnable_ids(con) == []


# -- resource blocking ---------------------------------------------------


def test_resource_undefined_blocks_step(con):
    """A step requiring a resource not listed in available_resource is excluded."""
    _insert_step(con, 2, 1, StepState.PENDING, safe=True, implied_need=Need.DEFAULT)
    con.execute("INSERT INTO step_resource (node, name, units) VALUES (2, 'gpu', 1)")
    assert _get_runnable_ids(con) == []


def test_resource_over_committed_blocks_step(con):
    """A step is excluded when available units minus in-use units is less than required."""
    _insert_step(con, 2, 1, StepState.PENDING, safe=True, implied_need=Need.DEFAULT)
    _insert_step(con, 3, 1, StepState.RUNNING, safe=True, implied_need=Need.DEFAULT)
    con.execute("INSERT INTO available_resource (name, units) VALUES ('gpu', 2)")
    con.execute("INSERT INTO step_resource (node, name, units) VALUES (2, 'gpu', 2)")
    con.execute("INSERT INTO step_resource (node, name, units) VALUES (3, 'gpu', 1)")
    # available(2) - running(1) = 1 < required(2) -> blocked
    assert _get_runnable_ids(con) == []


def test_resource_available_allows_step(con):
    """A step whose resource requirement fits within available capacity is returned."""
    _insert_step(con, 2, 1, StepState.PENDING, safe=True, implied_need=Need.DEFAULT)
    con.execute("INSERT INTO available_resource (name, units) VALUES ('gpu', 2)")
    con.execute("INSERT INTO step_resource (node, name, units) VALUES (2, 'gpu', 1)")
    assert _get_runnable_ids(con) == [2]


def test_resource_exactly_at_limit_allows_step(con):
    """A step requiring exactly the total available units (none in use) is returned."""
    _insert_step(con, 2, 1, StepState.PENDING, safe=True, implied_need=Need.DEFAULT)
    con.execute("INSERT INTO available_resource (name, units) VALUES ('gpu', 1)")
    con.execute("INSERT INTO step_resource (node, name, units) VALUES (2, 'gpu', 1)")
    assert _get_runnable_ids(con) == [2]


# -- ordering ------------------------------------------------------------


def test_ordering_plan_before_default(con):
    """PLAN steps are ordered before DEFAULT steps, regardless of tail_time.

    SELECT_NEXT_STEP carries a LIMIT 1 (only fetchone() is ever used on it),
    so only the top-priority candidate comes back;
    the assertion checks that the PLAN step wins.
    """
    _insert_step(
        con, 2, 1, StepState.PENDING, safe=True, implied_need=Need.DEFAULT, tail_time=100.0
    )
    _insert_step(con, 3, 1, StepState.PENDING, safe=True, implied_need=Need.PLAN, tail_time=1.0)
    ids = _get_runnable_ids(con)
    assert ids == [3]  # PLAN first despite lower tail_time


def test_ordering_higher_tail_time_first(con):
    """Within the same implied_need level, the step with higher _tail_time wins.

    SELECT_NEXT_STEP carries a LIMIT 1, so only the top-priority candidate comes back.
    """
    _insert_step(con, 2, 1, StepState.PENDING, safe=True, implied_need=Need.DEFAULT, tail_time=5.0)
    _insert_step(con, 3, 1, StepState.PENDING, safe=True, implied_need=Need.DEFAULT, tail_time=10.0)
    ids = _get_runnable_ids(con)
    assert ids == [3]


def test_ordering_node_tiebreaker(con):
    """When tail_time and implied_need are equal, the tie is broken by step.node order --
    the step_dispatch index's implicit primary-key suffix -- not by label.

    Node 10 is inserted before node 9 and gets the lexically *smaller* label ("echo 10" <
    "echo 9"), so this distinguishes the current node-based tie-break from the dropped
    label-based one: the old rule would have picked node 10 ("echo 10" first
    alphabetically); the current rule picks node 9 (lower step.node).

    SELECT_NEXT_STEP carries a LIMIT 1, so only the top-priority candidate comes back.
    """
    _insert_step(con, 10, 1, StepState.PENDING, safe=True, implied_need=Need.DEFAULT, tail_time=5.0)
    _insert_step(con, 9, 1, StepState.PENDING, safe=True, implied_need=Need.DEFAULT, tail_time=5.0)
    ids = _get_runnable_ids(con)
    assert ids == [9]


@pytest.mark.parametrize("need_threshold", [Need.OPTIONAL, Need.DEFAULT])
def test_select_next_step_uses_dispatch_index(con, need_threshold):
    """SELECT_NEXT_STEP walks step_dispatch in order and never sorts the candidate set.

    This is the whole point of materializing _has_hash/_ready: without it, SQLite falls
    back to scanning and sorting every matching row on each dispatch, which is exactly the
    hotspot this design removes. Regression-test the query plan directly so a future change
    that breaks the index match (e.g. an ORDER BY term outside the index) fails loudly here
    instead of only showing up as a production slowdown.

    Parametrized over both threshold bindings Workflow.need_threshold can supply (OPTIONAL
    without targets, DEFAULT with targets): the bound `step._implied_need > ?` term sits
    outside step_dispatch's WHERE clause, so it must not affect index eligibility either way.
    """
    plan = "\n".join(
        row[3]
        for row in con.execute(
            f"EXPLAIN QUERY PLAN {SELECT_NEXT_STEP}", (need_threshold.value,)
        ).fetchall()
    )
    assert "USING INDEX step_dispatch" in plan
    assert "TEMP B-TREE FOR ORDER BY" not in plan


# -----------------------------------------------------------------------
# Tests for SELECT_INPUTS
# -----------------------------------------------------------------------


def _get_inputs(con, sink_id):
    """Run SELECT_INPUTS for the given sink and return all rows as a list of dicts."""
    rows = con.execute(SELECT_INPUTS, (sink_id,)).fetchall()
    keys = ("label", "detached", "state", "amended", "hash")
    return [dict(zip(keys, row, strict=True)) for row in rows]


def test_select_inputs_no_inputs(con):
    """A step with no file dependencies returns an empty result."""
    _insert_step(con, 2, 1, StepState.PENDING)
    assert _get_inputs(con, 2) == []


def test_select_inputs_built_file(con):
    """A BUILT file dependency returns the correct row with is_amended=False."""
    _insert_step(con, 2, 1, StepState.PENDING)
    _insert_input_file(con, 3, 1, FileState.BUILT)
    _add_dep(con, 3, 2)
    rows = _get_inputs(con, 2)
    assert len(rows) == 1
    row = rows[0]
    assert row["label"] == "file_3.txt"
    assert row["detached"] == 0
    assert row["state"] == FileState.BUILT.value
    assert row["amended"] == 0
    file_hash = FileHash.from_json(row["hash"])
    assert file_hash.digest == b"\x01\x02\x03"
    assert file_hash.mode == 0o100644
    assert file_hash.mtime == pytest.approx(1000.0)
    assert file_hash.size == 100
    assert file_hash.inode == 42


def test_select_inputs_static_file(con):
    """A STATIC file dependency returns state=STATIC."""
    _insert_step(con, 2, 1, StepState.PENDING)
    _insert_input_file(con, 3, 1, FileState.STATIC)
    _add_dep(con, 3, 2)
    rows = _get_inputs(con, 2)
    assert len(rows) == 1
    assert rows[0]["state"] == FileState.STATIC.value


def test_select_inputs_awaited_file(con):
    """An AWAITED file dependency returns state=AWAITED with zeroed metadata."""
    _insert_step(con, 2, 1, StepState.PENDING)
    _insert_input_file(con, 3, 1, FileState.AWAITED)
    _add_dep(con, 3, 2)
    rows = _get_inputs(con, 2)
    assert len(rows) == 1
    assert rows[0]["state"] == FileState.AWAITED.value
    assert rows[0]["hash"] is None


def test_select_inputs_amended_flag_true(con):
    """An amended dependency returns amended=1."""
    _insert_step(con, 2, 1, StepState.PENDING)
    _insert_input_file(con, 3, 1, FileState.BUILT)
    dep_id = _add_dep_returning_id(con, 3, 2)
    _mark_dep_amended(con, dep_id)
    rows = _get_inputs(con, 2)
    assert rows[0]["amended"] == 1


def test_select_inputs_amended_flag_false(con):
    """A non-amended dependency returns amended=0."""
    _insert_step(con, 2, 1, StepState.PENDING)
    _insert_input_file(con, 3, 1, FileState.BUILT)
    _add_dep(con, 3, 2)
    rows = _get_inputs(con, 2)
    assert rows[0]["amended"] == 0


def test_select_inputs_detached_file(con):
    """A detached file dependency returns detached=1."""
    _insert_step(con, 2, 1, StepState.PENDING)
    _insert_input_file(con, 3, 1, FileState.BUILT, detached=True)
    _add_dep(con, 3, 2)
    rows = _get_inputs(con, 2)
    assert rows[0]["detached"] == 1


def test_select_inputs_multiple_files(con):
    """Multiple file dependencies return one row per file, ordered by label."""
    _insert_step(con, 2, 1, StepState.PENDING)
    _insert_input_file(con, 3, 1, FileState.BUILT)
    _insert_input_file(con, 4, 1, FileState.STATIC)
    _insert_input_file(con, 5, 1, FileState.AWAITED)
    _add_dep(con, 3, 2)
    _add_dep(con, 4, 2)
    _add_dep(con, 5, 2)
    rows = _get_inputs(con, 2)
    assert len(rows) == 3
    states = {r["label"]: r["state"] for r in rows}
    assert states["file_3.txt"] == FileState.BUILT.value
    assert states["file_4.txt"] == FileState.STATIC.value
    assert states["file_5.txt"] == FileState.AWAITED.value


def test_select_inputs_only_returns_inputs_for_queried_sink(con):
    """Inputs of another step are not included in the result for the queried step."""
    _insert_step(con, 2, 1, StepState.PENDING)
    _insert_step(con, 3, 1, StepState.PENDING)
    _insert_input_file(con, 4, 1, FileState.BUILT)  # input to step 2
    _insert_input_file(con, 5, 1, FileState.STATIC)  # input to step 3 only
    _add_dep(con, 4, 2)
    _add_dep(con, 5, 3)
    rows = _get_inputs(con, 2)
    assert len(rows) == 1
    assert rows[0]["label"] == "file_4.txt"


# -----------------------------------------------------------------------
# Tests for SELECT_RESOURCE_COUNTS
# -----------------------------------------------------------------------


def _get_resource_counts(con):
    """Run SELECT_RESOURCE_COUNTS and return a dict mapping name -> (used, available)."""
    return {
        name: (used, available)
        for name, used, available in con.execute(SELECT_RESOURCE_COUNTS).fetchall()
    }


def test_resource_counts_no_resources(con):
    """When available_resource is empty the query returns no rows."""
    assert _get_resource_counts(con) == {}


def test_resource_counts_no_running_steps(con):
    """A resource with no running steps reports used=0."""
    con.execute("INSERT INTO available_resource (name, units) VALUES ('gpu', 4)")
    counts = _get_resource_counts(con)
    assert counts == {"gpu": (0, 4)}


def test_resource_counts_one_running_step(con):
    """Units consumed by a single RUNNING step are reflected in used."""
    _insert_step(con, 2, 1, StepState.RUNNING)
    con.execute("INSERT INTO available_resource (name, units) VALUES ('gpu', 4)")
    con.execute("INSERT INTO step_resource (node, name, units) VALUES (2, 'gpu', 1)")
    counts = _get_resource_counts(con)
    assert counts == {"gpu": (1, 4)}


def test_resource_counts_multiple_running_steps_summed(con):
    """Units from several RUNNING steps are summed into used."""
    _insert_step(con, 2, 1, StepState.RUNNING)
    _insert_step(con, 3, 1, StepState.RUNNING)
    con.execute("INSERT INTO available_resource (name, units) VALUES ('gpu', 8)")
    con.execute("INSERT INTO step_resource (node, name, units) VALUES (2, 'gpu', 2)")
    con.execute("INSERT INTO step_resource (node, name, units) VALUES (3, 'gpu', 3)")
    counts = _get_resource_counts(con)
    assert counts == {"gpu": (5, 8)}


def test_resource_counts_non_running_steps_not_counted(con):
    """PENDING, SUCCEEDED, and FAILED steps do not contribute to used."""
    _insert_step(con, 2, 1, StepState.PENDING)
    _insert_step(con, 3, 1, StepState.SUCCEEDED)
    _insert_step(con, 4, 1, StepState.FAILED)
    con.execute("INSERT INTO available_resource (name, units) VALUES ('gpu', 4)")
    con.execute("INSERT INTO step_resource (node, name, units) VALUES (2, 'gpu', 1)")
    con.execute("INSERT INTO step_resource (node, name, units) VALUES (3, 'gpu', 1)")
    con.execute("INSERT INTO step_resource (node, name, units) VALUES (4, 'gpu', 1)")
    counts = _get_resource_counts(con)
    assert counts == {"gpu": (0, 4)}


def test_resource_counts_multiple_resources_independent(con):
    """Each resource is counted independently; a running step only affects its own resource."""
    _insert_step(con, 2, 1, StepState.RUNNING)
    con.execute("INSERT INTO available_resource (name, units) VALUES ('gpu', 4)")
    con.execute("INSERT INTO available_resource (name, units) VALUES ('cpu', 16)")
    con.execute("INSERT INTO step_resource (node, name, units) VALUES (2, 'gpu', 2)")
    counts = _get_resource_counts(con)
    assert counts == {"gpu": (2, 4), "cpu": (0, 16)}


def test_resource_counts_resource_not_in_available_excluded(con):
    """A resource used by a running step but absent from available_resource is not returned."""
    _insert_step(con, 2, 1, StepState.RUNNING)
    # 'secret' is not in available_resource, so it must not appear in the result.
    con.execute("INSERT INTO available_resource (name, units) VALUES ('gpu', 4)")
    con.execute("INSERT INTO step_resource (node, name, units) VALUES (2, 'secret', 1)")
    counts = _get_resource_counts(con)
    assert "secret" not in counts


# -----------------------------------------------------------------------
# Tests for UNAVAILABLE_INPUT
#
# UNAVAILABLE_INPUT is a correlated subquery parameterized by `node.i` from
# the outer query.  The helper below wraps it with
# `SELECT EXISTS (...) FROM node WHERE node.i = ?` so each test can drive it
# directly against a known sink step.
#
# The three blocking branches are:
#   VOLATILE      – any dep type, any detach state
#   Case 1        – amended AND NOT detached AND state IN (AWAITED, OUTDATED)
#   Case 2        – initial (not amended) AND (detached OR state not in {BUILT, STATIC})
# -----------------------------------------------------------------------


def _has_unavailable_input(con, sink_id):
    """Return whether the given sink has at least one unavailable input."""
    row = con.execute(
        f"SELECT EXISTS ({UNAVAILABLE_INPUT}) FROM node WHERE node.i = ?",
        (sink_id,),
    ).fetchone()
    return bool(row[0])


def test_unavailable_input_no_inputs(con):
    """A step with no file inputs has no unavailable inputs."""
    _insert_step(con, 2, 1, StepState.PENDING)
    assert not _has_unavailable_input(con, 2)


def test_unavailable_input_volatile_initial(con):
    """VOLATILE top-level condition: initial dep on a VOLATILE file -> unavailable."""
    _insert_step(con, 2, 1, StepState.PENDING)
    _insert_input_file(con, 3, 1, FileState.VOLATILE)
    _add_dep(con, 3, 2)
    assert _has_unavailable_input(con, 2)


def test_unavailable_input_volatile_amended(con):
    """VOLATILE always blocks regardless of amended status."""
    _insert_step(con, 2, 1, StepState.PENDING)
    _insert_input_file(con, 3, 1, FileState.VOLATILE)
    dep_id = _add_dep_returning_id(con, 3, 2)
    _mark_dep_amended(con, dep_id)
    assert _has_unavailable_input(con, 2)


def test_unavailable_input_case1_amended_nondetached_awaited(con):
    """Case 1: amended, non-detached, AWAITED -> unavailable."""
    _insert_step(con, 2, 1, StepState.PENDING)
    _insert_input_file(con, 3, 1, FileState.AWAITED)
    dep_id = _add_dep_returning_id(con, 3, 2)
    _mark_dep_amended(con, dep_id)
    assert _has_unavailable_input(con, 2)


def test_unavailable_input_case1_amended_nondetached_outdated(con):
    """Case 1: amended, non-detached, OUTDATED -> unavailable."""
    _insert_step(con, 2, 1, StepState.PENDING)
    _insert_input_file(con, 3, 1, FileState.OUTDATED)
    dep_id = _add_dep_returning_id(con, 3, 2)
    _mark_dep_amended(con, dep_id)
    assert _has_unavailable_input(con, 2)


def test_unavailable_input_case1_miss_amended_nondetached_missing(con):
    """Amended, non-detached, MISSING: MISSING not in {AWAITED, OUTDATED} so case 1 does not fire;
    case 2 does not fire because the dep is amended -> available."""
    _insert_step(con, 2, 1, StepState.PENDING)
    _insert_input_file(con, 3, 1, FileState.MISSING)
    dep_id = _add_dep_returning_id(con, 3, 2)
    _mark_dep_amended(con, dep_id)
    assert not _has_unavailable_input(con, 2)


def test_unavailable_input_case1_miss_amended_detached_awaited(con):
    """Amended, detached, AWAITED: case 1 requires NOT detached so it does not fire;
    case 2 requires an initial dep so it also does not fire -> available."""
    _insert_step(con, 2, 1, StepState.PENDING)
    _insert_input_file(con, 3, 1, FileState.AWAITED, detached=True)
    dep_id = _add_dep_returning_id(con, 3, 2)
    _mark_dep_amended(con, dep_id)
    assert not _has_unavailable_input(con, 2)


def test_unavailable_input_case2_initial_nondetached_awaited(con):
    """Case 2: initial, non-detached, AWAITED (not in {BUILT, STATIC}) -> unavailable."""
    _insert_step(con, 2, 1, StepState.PENDING)
    _insert_input_file(con, 3, 1, FileState.AWAITED)
    _add_dep(con, 3, 2)
    assert _has_unavailable_input(con, 2)


def test_unavailable_input_case2_initial_nondetached_outdated(con):
    """Case 2: initial, non-detached, OUTDATED -> unavailable."""
    _insert_step(con, 2, 1, StepState.PENDING)
    _insert_input_file(con, 3, 1, FileState.OUTDATED)
    _add_dep(con, 3, 2)
    assert _has_unavailable_input(con, 2)


def test_unavailable_input_case2_initial_nondetached_missing(con):
    """Case 2: initial, non-detached, MISSING -> unavailable."""
    _insert_step(con, 2, 1, StepState.PENDING)
    _insert_input_file(con, 3, 1, FileState.MISSING)
    _add_dep(con, 3, 2)
    assert _has_unavailable_input(con, 2)


def test_unavailable_input_case2_miss_initial_nondetached_built(con):
    """Case 2: initial, non-detached, BUILT (in {BUILT, STATIC}) -> available."""
    _insert_step(con, 2, 1, StepState.PENDING)
    _insert_input_file(con, 3, 1, FileState.BUILT)
    _add_dep(con, 3, 2)
    assert not _has_unavailable_input(con, 2)


def test_unavailable_input_case2_miss_initial_nondetached_static(con):
    """Case 2: initial, non-detached, STATIC -> available."""
    _insert_step(con, 2, 1, StepState.PENDING)
    _insert_input_file(con, 3, 1, FileState.STATIC)
    _add_dep(con, 3, 2)
    assert not _has_unavailable_input(con, 2)


def test_unavailable_input_case2_initial_detached_built(con):
    """Case 2: initial, detached -> unavailable regardless of file state (detached triggers)."""
    _insert_step(con, 2, 1, StepState.PENDING)
    _insert_input_file(con, 3, 1, FileState.BUILT, detached=True)
    _add_dep(con, 3, 2)
    assert _has_unavailable_input(con, 2)


def test_unavailable_input_multiple_one_blocking(con):
    """Multiple inputs: one non-blocking and one blocking -> unavailable (EXISTS semantics)."""
    _insert_step(con, 2, 1, StepState.PENDING)
    _insert_input_file(con, 3, 1, FileState.BUILT)
    _insert_input_file(con, 4, 1, FileState.AWAITED)
    _add_dep(con, 3, 2)
    _add_dep(con, 4, 2)
    assert _has_unavailable_input(con, 2)


def test_unavailable_input_multiple_none_blocking(con):
    """Multiple inputs, all non-blocking -> available."""
    _insert_step(con, 2, 1, StepState.PENDING)
    _insert_input_file(con, 3, 1, FileState.BUILT)
    _insert_input_file(con, 4, 1, FileState.STATIC)
    _add_dep(con, 3, 2)
    _add_dep(con, 4, 2)
    assert not _has_unavailable_input(con, 2)


# -----------------------------------------------------------------------
# Tests for CHECKING state: SELECT_SAFE_UPDATE
# -----------------------------------------------------------------------


def test_checking_creator_keeps_product_unsafe(con):
    """A product of a CHECKING step keeps _safe=0 (CHECKING is not a safe creator state)."""
    _insert_step(con, 2, 1, StepState.CHECKING, check_safe=True)
    _insert_step(con, 3, 2, StepState.PENDING)
    _run_update_meta_safe(con)
    assert _get_safe(con)[3] == 0


def test_checking_in_chain_blocks_safe(con):
    """Safety does not propagate through a CHECKING creator: root -> A(CHECKING) -> B(PENDING)."""
    _insert_step(con, 2, 1, StepState.CHECKING, check_safe=True)
    _insert_step(con, 3, 2, StepState.PENDING)
    _run_update_meta_safe(con)
    assert _get_safe(con)[3] == 0


# -----------------------------------------------------------------------
# Tests for SELECT_NEXT_STEP's checkable (hash-checking) path, and UNAVAILABLE_INPUT
# -----------------------------------------------------------------------


def _insert_step_hash(con, node_id):
    """Set a minimal hash value so the step is considered checkable."""
    con.execute("INSERT OR REPLACE INTO step_hash VALUES (?, '{}')", (node_id,))


def _get_checkable_ids(con, need_threshold=Need.OPTIONAL):
    """Run SELECT_NEXT_STEP and return the ids of results
    dispatched via the checkable (hash-checking) path.

    Every scenario in this section inserts at most one PENDING step,
    so filtering SELECT_NEXT_STEP's (at most one) result by has_hash
    is equivalent to running the old standalone SELECT_CHECKABLE_STEPS query.
    A test with multiple simultaneously-eligible PENDING steps
    should query SELECT_NEXT_STEP directly instead of relying on this helper,
    since SELECT_NEXT_STEP's LIMIT 1 only ever returns
    the single highest-priority candidate overall (checkable steps always win),
    not the best candidate per path.

    See `_get_runnable_ids` for `need_threshold`.
    """
    rows = con.execute(SELECT_NEXT_STEP, (need_threshold.value,)).fetchall()
    return [row[0] for row in rows if row[2]]


def _has_unavailable_input(con, sink_id):
    """Return whether the given sink has at least one unavailable input."""
    row = con.execute(
        f"SELECT EXISTS ({UNAVAILABLE_INPUT}) FROM node WHERE node.i = ?",
        (sink_id,),
    ).fetchone()
    return bool(row[0])


def test_checkable_step_no_inputs_with_hash(con):
    """A PENDING safe step with a stored hash and no inputs is checkable."""
    _insert_step(con, 2, 1, StepState.PENDING, safe=True, implied_need=Need.DEFAULT)
    _insert_step_hash(con, 2)
    assert _get_checkable_ids(con) == [2]


def test_checkable_step_no_hash_not_checkable(con):
    """A PENDING safe step without a stored hash is NOT checkable (must execute)."""
    _insert_step(con, 2, 1, StepState.PENDING, safe=True, implied_need=Need.DEFAULT)
    assert _get_checkable_ids(con) == []


def test_checkable_step_bypasses_hold(con):
    """A PENDING step with a stored hash is checkable even while _safe=0 due to a hold,
    as long as _safe_ignoring_hold is 1 (i.e. the hold is the *only* reason it's unsafe).

    This exercises STEP_DISPATCH_WHERE's `_safe OR (_has_hash AND _safe_ignoring_hold)`
    disjunct, which lets a cheap hash check bypass an active hold().
    """
    _insert_step(
        con,
        2,
        1,
        StepState.PENDING,
        safe=False,
        safe_ignoring_hold=True,
        implied_need=Need.DEFAULT,
    )
    _insert_step_hash(con, 2)
    assert _get_checkable_ids(con) == [2]


def test_unsafe_for_other_reasons_step_not_checkable_despite_hash(con):
    """A PENDING step with a stored hash but _safe_ignoring_hold=0 stays uncheckable.

    Confirms the bypass only exempts the hold itself: if the step would be unsafe even
    ignoring any hold (e.g. a real ancestor failure), it must not be dispatched.
    """
    _insert_step(
        con,
        2,
        1,
        StepState.PENDING,
        safe=False,
        safe_ignoring_hold=False,
        implied_need=Need.DEFAULT,
    )
    _insert_step_hash(con, 2)
    assert _get_checkable_ids(con) == []


def test_runjob_stays_gated_by_hold_despite_checkable_sibling(con):
    """A hash-less (must-run) step stays blocked by an active hold, even alongside a
    checkable sibling that bypasses it -- SELECT_NEXT_STEP's LIMIT 1 must not confuse the
    two: the checkable one is returned, the runnable one is not (yet) a candidate.
    """
    _insert_step(
        con,
        2,
        1,
        StepState.PENDING,
        safe=False,
        safe_ignoring_hold=True,
        implied_need=Need.DEFAULT,
        tail_time=100.0,
    )
    _insert_step_hash(con, 2)
    _insert_step(
        con,
        3,
        1,
        StepState.PENDING,
        safe=False,
        safe_ignoring_hold=True,
        implied_need=Need.DEFAULT,
        tail_time=1.0,
    )
    assert _get_checkable_ids(con) == [2]
    assert _get_runnable_ids(con) == []


def test_running_step_not_checkable(con):
    """A RUNNING step is excluded — only PENDING steps are candidates."""
    _insert_step(con, 2, 1, StepState.RUNNING, safe=True, implied_need=Need.DEFAULT)
    _insert_step_hash(con, 2)
    assert _get_checkable_ids(con) == []


def test_checking_step_not_checkable(con):
    """A CHECKING step is excluded — only PENDING steps are candidates."""
    _insert_step(con, 2, 1, StepState.CHECKING, safe=True, implied_need=Need.DEFAULT)
    _insert_step_hash(con, 2)
    assert _get_checkable_ids(con) == []


def test_checkable_step_blocked_by_unavailable_initial_input(con):
    """A step with a hash but an unavailable initial (non-amended) input is NOT checkable."""
    _insert_step(con, 2, 1, StepState.PENDING, safe=True, implied_need=Need.DEFAULT, ready=False)
    _insert_step_hash(con, 2)
    _insert_input_file(con, 3, 1, FileState.AWAITED)
    _add_dep(con, 3, 2)
    _recompute_ready(con)
    assert _get_checkable_ids(con) == []


def test_checkable_step_with_ready_initial_and_unready_amended_input(con):
    """A step with a hash: ready initial input + unready amended input IS checkable.

    This is the ValidateAmendedJob case: amended inputs not yet ready, but we can
    still validate that the initial inputs haven't changed (without resource slots).
    """
    _insert_step(con, 2, 1, StepState.PENDING, safe=True, implied_need=Need.DEFAULT, ready=False)
    _insert_step_hash(con, 2)
    # Ready initial input
    _insert_input_file(con, 3, 1, FileState.STATIC)
    _add_dep(con, 3, 2)
    # Unready amended input (MISSING — case 1 of UNAVAILABLE_INPUT blocks but not INITIAL)
    _insert_input_file(con, 4, 1, FileState.MISSING)
    dep_id = _add_dep_returning_id(con, 4, 2)
    _mark_dep_amended(con, dep_id)
    _recompute_ready(con)
    # MISSING amended inputs are NOT blocked by UNAVAILABLE_INPUT
    # (case 1 only blocks AWAITED/OUTDATED),
    # so both the runnable and checkable paths of SELECT_NEXT_STEP allow them.
    assert _get_checkable_ids(con) == [2]


def test_checkable_step_with_hash_and_missing_resource(con):
    """A step with a hash is checkable even when its resource is NOT available.

    This is the core property: the checkable path of SELECT_NEXT_STEP
    does not check resource availability,
    so PENDING steps with hashes are scheduled for CHECKING
    without waiting for resources.
    This ensures skipping is never blocked by named resource restrictions.
    """
    _insert_step(con, 2, 1, StepState.PENDING, safe=True, implied_need=Need.DEFAULT)
    _insert_step_hash(con, 2)
    con.execute("INSERT INTO step_resource (node, name, units) VALUES (2, 'gpu', 1)")
    # No row in available_resource → resource is undefined → the runnable path would block
    assert _get_runnable_ids(con) == []
    # But the checkable path ignores resources → step is still checkable
    assert _get_checkable_ids(con) == [2]


def test_checkable_step_with_hash_and_exhausted_resource(con):
    """A step with a hash is checkable even when its resource pool is fully consumed."""
    _insert_step(con, 2, 1, StepState.PENDING, safe=True, implied_need=Need.DEFAULT)
    _insert_step_hash(con, 2)
    _insert_step(con, 3, 1, StepState.RUNNING, safe=True, implied_need=Need.DEFAULT)
    con.execute("INSERT INTO available_resource (name, units) VALUES ('gpu', 1)")
    con.execute("INSERT INTO step_resource (node, name, units) VALUES (2, 'gpu', 1)")
    con.execute("INSERT INTO step_resource (node, name, units) VALUES (3, 'gpu', 1)")
    # The runnable path would block: available(1) - running(1) = 0 < required(1)
    assert _get_runnable_ids(con) == []
    # But the checkable path ignores resources → step is checkable
    assert _get_checkable_ids(con) == [2]


def test_unavailable_input_blocks_on_amended_awaited(con):
    """UNAVAILABLE_INPUT blocks on amended AWAITED inputs."""
    _insert_step(con, 2, 1, StepState.PENDING)
    _insert_input_file(con, 3, 1, FileState.AWAITED)
    dep_id = _add_dep_returning_id(con, 3, 2)
    _mark_dep_amended(con, dep_id)
    assert _has_unavailable_input(con, 2)


def test_unavailable_input_blocks_on_amended_outdated(con):
    """UNAVAILABLE_INPUT blocks on amended OUTDATED inputs."""
    _insert_step(con, 2, 1, StepState.PENDING)
    _insert_input_file(con, 3, 1, FileState.OUTDATED)
    dep_id = _add_dep_returning_id(con, 3, 2)
    _mark_dep_amended(con, dep_id)
    assert _has_unavailable_input(con, 2)


def test_unavailable_input_blocks_on_initial_awaited(con):
    """UNAVAILABLE_INPUT blocks on an initial (non-amended) AWAITED input."""
    _insert_step(con, 2, 1, StepState.PENDING)
    _insert_input_file(con, 3, 1, FileState.AWAITED)
    _add_dep(con, 3, 2)
    assert _has_unavailable_input(con, 2)


def test_unavailable_input_blocks_on_volatile(con):
    """UNAVAILABLE_INPUT blocks on VOLATILE inputs (initial or amended)."""
    _insert_step(con, 2, 1, StepState.PENDING)
    _insert_input_file(con, 3, 1, FileState.VOLATILE)
    _add_dep(con, 3, 2)
    assert _has_unavailable_input(con, 2)


# -----------------------------------------------------------------------
# Tests for start_times/stop_times bookkeeping
# -----------------------------------------------------------------------


async def test_record_stop_time_writes_stop_time_and_prunes_start_time(wfs: Workflow):
    async with wfs.db:
        wfs.define_step(wfs.root, "plan")
        plan = wfs.find(Step, "plan")
        wfs.define_step(plan, "echo")
        wfs.define_step(plan, "other")
        step = wfs.find(Step, "echo")
        other = wfs.find(Step, "other")
        step.set_state(StepState.RUNNING)
        other.set_state(StepState.RUNNING)

        scheduler = Scheduler(wfs, db=wfs.db)
        scheduler.start_times[step.i] = 100
        # A second step is still running, so record_stop_time() below must not treat
        # concurrency as having dropped to zero and prune the stop time it just wrote.
        scheduler.start_times[other.i] = 200

        scheduler.record_stop_time(step.i, succeeded=True)
        assert step.i not in scheduler.start_times
        assert step.i in scheduler.stop_times


async def test_record_stop_time_no_stop_time_on_failure(wfs: Workflow):
    async with wfs.db:
        wfs.define_step(wfs.root, "echo")
        step = wfs.find(Step, "echo")
        step.set_state(StepState.RUNNING)

        scheduler = Scheduler(wfs, db=wfs.db)
        scheduler.start_times[step.i] = 100

        scheduler.record_stop_time(step.i, succeeded=False)
        assert step.i not in scheduler.start_times
        assert step.i not in scheduler.stop_times


async def test_stop_times_cleared_when_no_steps_running(wfs: Workflow):
    """Once the last RUNNING step finishes, every stop_times entry is dropped: nothing
    running means nothing can race with a not-yet-dispatched step."""
    async with wfs.db:
        wfs.define_step(wfs.root, "plan")
        plan = wfs.find(Step, "plan")
        wfs.define_step(plan, "a")
        wfs.define_step(plan, "b")
        step_a = wfs.find(Step, "a")
        step_b = wfs.find(Step, "b")
        step_a.set_state(StepState.RUNNING)
        step_b.set_state(StepState.RUNNING)

        scheduler = Scheduler(wfs, db=wfs.db)
        # record_stop_time() now tracks concurrency via start_times, not step DB state
        # (see pop_runnable_job()), so populate it the way dispatch would.
        scheduler.start_times[step_a.i] = time.monotonic_ns()
        scheduler.start_times[step_b.i] = time.monotonic_ns()

        # step_a finishes while step_b is still RUNNING: stop_times must survive.
        step_a.set_state(StepState.SUCCEEDED)
        scheduler.record_stop_time(step_a.i, succeeded=True)
        assert step_a.i in scheduler.stop_times

        # step_b finishes too: nothing RUNNING anymore, so stop_times is cleared entirely.
        step_b.set_state(StepState.SUCCEEDED)
        scheduler.record_stop_time(step_b.i, succeeded=True)
        assert scheduler.stop_times == {}


# -----------------------------------------------------------------------
# Tests for deferred step-duration writes (new_durations / build_completed)
# -----------------------------------------------------------------------


def _get_duration_and_check_after(wfs: Workflow, step: Step) -> tuple[float, int]:
    return wfs.db.execute(
        "SELECT duration, _check_after FROM step WHERE node = ?", (step.i,)
    ).fetchone()


async def test_job_completed_accumulates_duration_without_writing_db(wfs: Workflow):
    async with wfs.db:
        wfs.define_step(wfs.root, "echo")
        step = wfs.find(Step, "echo")

    scheduler = Scheduler(wfs, db=wfs.db, use_duration=True)
    job = RunJob(step, [], [], None, job_i=0)
    scheduler.jobs[job.job_i] = step

    await scheduler.job_completed(job)

    assert step.i in scheduler.new_durations
    async with wfs.db:
        duration, _check_after = _get_duration_and_check_after(wfs, step)
    assert duration == 1.0  # schema default, unaffected until build_completed() runs


async def test_job_completed_no_op_when_use_duration_disabled(wfs: Workflow):
    async with wfs.db:
        wfs.define_step(wfs.root, "echo")
        step = wfs.find(Step, "echo")

    scheduler = Scheduler(wfs, db=wfs.db, use_duration=False)
    job = RunJob(step, [], [], None, job_i=0)
    scheduler.jobs[job.job_i] = step

    await scheduler.job_completed(job)

    assert scheduler.new_durations == {}


async def test_build_completed_skips_small_relative_change(wfs: Workflow):
    async with wfs.db:
        wfs.define_step(wfs.root, "echo")
        step = wfs.find(Step, "echo")
        wfs.db.execute("UPDATE step SET _check_after = 0 WHERE node = ?", (step.i,))

        scheduler = Scheduler(wfs, db=wfs.db, use_duration=True)
        scheduler.new_durations[step.i] = 1.05  # within 10% of the schema default (1.0)

        scheduler.build_completed()

        duration, check_after = _get_duration_and_check_after(wfs, step)
    assert duration == 1.0
    assert check_after == 0
    assert scheduler.new_durations == {}


async def test_build_completed_writes_large_relative_change(wfs: Workflow):
    async with wfs.db:
        wfs.define_step(wfs.root, "echo")
        step = wfs.find(Step, "echo")
        wfs.db.execute("UPDATE step SET _check_after = 0 WHERE node = ?", (step.i,))

        scheduler = Scheduler(wfs, db=wfs.db, use_duration=True)
        scheduler.new_durations[step.i] = 2.0  # 100% change from the schema default (1.0)

        scheduler.build_completed()

        duration, check_after = _get_duration_and_check_after(wfs, step)
    assert duration == 2.0
    assert check_after == 1  # step_flag_check_after_duration fired
    assert scheduler.new_durations == {}


async def test_second_job_completed_overwrites_pending_duration(wfs: Workflow):
    """A step that re-runs within one phase (validate-amended path) keeps only its latest
    measured duration in the accumulation buffer -- last value wins, same as today's
    last-write-wins per-row UPDATE."""
    async with wfs.db:
        wfs.define_step(wfs.root, "echo")
        step = wfs.find(Step, "echo")

    scheduler = Scheduler(wfs, db=wfs.db, use_duration=True)

    job1 = RunJob(step, [], [], None, create_time=1000.0, job_i=0)
    scheduler.jobs[job1.job_i] = step
    await scheduler.job_completed(job1)
    duration_after_first = scheduler.new_durations[step.i]

    job2 = RunJob(step, [], [], None, create_time=2000.0, job_i=1)
    scheduler.jobs[job2.job_i] = step
    await scheduler.job_completed(job2)
    duration_after_second = scheduler.new_durations[step.i]

    assert len(scheduler.new_durations) == 1
    assert duration_after_second != duration_after_first


# -----------------------------------------------------------------------
# Tests for ran_concurrently
# -----------------------------------------------------------------------


async def test_ran_concurrently_true_when_consumer_starts_before_producer_stops(wfs: Workflow):
    scheduler = Scheduler(wfs, db=wfs.db)
    scheduler.start_times[2] = 100
    scheduler.stop_times[1] = 200
    assert scheduler.ran_concurrently(1, 2)


async def test_ran_concurrently_tie_counts_as_overlapping(wfs: Workflow):
    scheduler = Scheduler(wfs, db=wfs.db)
    scheduler.start_times[2] = 150
    scheduler.stop_times[1] = 150
    assert scheduler.ran_concurrently(1, 2)


async def test_ran_concurrently_false_when_consumer_starts_after_producer_stops(wfs: Workflow):
    scheduler = Scheduler(wfs, db=wfs.db)
    scheduler.start_times[2] = 200
    scheduler.stop_times[1] = 100
    assert not scheduler.ran_concurrently(1, 2)


async def test_ran_concurrently_false_when_producer_stop_time_missing(wfs: Workflow):
    scheduler = Scheduler(wfs, db=wfs.db)
    scheduler.start_times[2] = 100
    assert not scheduler.ran_concurrently(1, 2)


async def test_ran_concurrently_false_when_consumer_start_time_missing(wfs: Workflow):
    scheduler = Scheduler(wfs, db=wfs.db)
    scheduler.stop_times[1] = 100
    assert not scheduler.ran_concurrently(1, 2)


async def test_child_of_running_step_dispatched_as_soon_as_created(wfp: Workflow):
    """A step created by an already-RUNNING creator is dispatched right away, as soon as its
    own inputs are ready, without waiting for the creator to settle into a new state.

    Regression test for a bug where `_safe` was only (re)computed for a step's *existing*
    children when the creator's own `_check_safe` flag was processed by `_update_meta_safe()`.
    A step's `_check_safe` flag is set once, at the moment it is dispatched to RUNNING, and
    used to be cleared by the very next `_update_meta_safe()` pass (which the builder runs on
    every `pop_runnable_job()` call) -- typically well before the creator's script has had a
    chance to create any children. When the creator then created a *new* child while
    remaining RUNNING (the common case for a `plan.py` calling `step()`), nothing re-flagged
    the creator's `_check_safe`, so the new child's `_safe` was never (re)computed from the
    creator's RUNNING state until the creator transitioned state again (typically on
    completion). `SELECT_SAFE_UPDATE` now seeds its recursive walk one level up, at each
    flagged step's *creator*, using the creator's own already-computed `_safe`/state, so a
    freshly created child's own `_safe` is derived the moment *it* is flagged (at creation),
    regardless of whether its creator is flagged in the same pass.
    """
    scheduler = Scheduler(wfp, db=wfp.db)
    await scheduler.initialize(None)

    # Unlike the real boot step (created with `safe=True` in `Workflow.__init__`, see
    # `workflow.py:217`), the `wfp` fixture defines "./plan.py" without it, so it starts
    # with `_safe=0` like any other step. Root has no `step` row, so `_update_meta_safe()`
    # can never derive the boot step's own `_safe` from its creator. Bring it in line with
    # the real bootstrap so the scheduler can dispatch it in the first place.
    # `_safe_ignoring_hold` is set alongside `_safe`, matching `Step.create()`'s own seeding
    # of a `safe=True` step, and to satisfy the `_safe_ignoring_hold >= _safe` table CHECK.
    async with wfp.db:
        wfp.db.execute(
            "UPDATE step SET _safe = 1, _safe_ignoring_hold = 1, _check_safe = 0 WHERE node = ?",
            (wfp.find(Step, "./plan.py").i,),
        )

    # Dispatch the boot step ("./plan.py"): the only runnable step so far.
    # (`pop_runnable_job` acquires `wfp.db` itself, so it must not be called from
    # within an outer `async with wfp.db:` block.)
    job = await scheduler.pop_runnable_job()
    assert job is not None
    plan = job.step
    async with wfp.db:
        assert plan.get_state() == StepState.RUNNING

    # A second poll (as the builder's job loop would do immediately after) finds nothing
    # left to run, and as a side effect clears `plan`'s `_check_safe` flag.
    assert await scheduler.pop_runnable_job() is None

    # `plan`'s script now creates a child step, as `step()` would via RPC, well after
    # `plan`'s own `_check_safe` flag was already cleared above.
    async with wfp.db:
        wfp.define_step(plan, "sub")
        sub = wfp.find(Step, "sub")
        assert sub.get_state() == StepState.PENDING

    # `plan` (its creator) is RUNNING and `sub` has no unmet inputs, so it is dispatched
    # right away -- no need to wait for `plan` to transition state again.
    job = await scheduler.pop_runnable_job()
    assert job is not None
    assert job.step.i == sub.i


async def test_pop_runnable_job_dispatches_checkable_step_to_checking(wfs: Workflow):
    """A PENDING step with a stored hash is dispatched
    via `pop_runnable_job()` straight to `CHECKING`.

    The raw-SQL unit tests above cover `SELECT_NEXT_STEP`'s
    checkable-path eligibility rules directly,
    but none of them drive `pop_runnable_job()` itself through this branch end-to-end --
    this closes that gap for the exact method production code calls.
    """
    scheduler = Scheduler(wfs, db=wfs.db)
    await scheduler.initialize(None)

    async with wfs.db:
        wfs.define_step(wfs.root, "echo", safe=True)
        step = wfs.find(Step, "echo")
        assert step.get_state() == StepState.PENDING
        step.set_hash(StepHash(b"deadbeef"))

    job = await scheduler.pop_runnable_job()
    assert job is not None
    assert job.step.i == step.i
    async with wfs.db:
        assert step.get_state() == StepState.CHECKING
