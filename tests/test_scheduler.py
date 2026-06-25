# StepUp Core provides the basic framework for the StepUp build tool.
# Copyright 2024-2026 Toon Verstraelen
#
# This file is part of StepUp Core.
#
# StepUp Core is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 3
# of the License, or (at your option) any later version.
#
# StepUp Core is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, see <http://www.gnu.org/licenses/>
#
# --
"""Unit tests for stepup.core.scheduler."""

import pytest

from stepup.core.enums import FileState, Need, StepState
from stepup.core.file import FILE_SCHEMA
from stepup.core.scheduler import (
    APPLY_UPDATE_CHECK_AFTER,
    DROP_CHECK_AFTER,
    DROP_UPDATE_CHECK_AFTER,
    EMPTY_CHECK_AFTER,
    INIT_CHECK_AFTER,
    INIT_UPDATE_CHECK_AFTER,
    PROPAGATE_UPDATE_CHECK_AFTER,
    PRUNE_DETACHED_CHECK_AFTER,
    PRUNE_REDUNDANT_CHECK_AFTER,
    RECURSIVE_UPDATE_SAFE,
    SELECT_CHECKABLE_STEPS,
    SELECT_INPUTS,
    SELECT_PENDING_REASONS,
    SELECT_RESOURCE_COUNTS,
    SELECT_RUNNABLE_STEPS,
    SELECT_UPDATE_CHECK_AFTER,
    UNAVAILABLE_INPUT,
)
from stepup.core.sqlite3 import connect
from stepup.core.step import STEP_SCHEMA
from stepup.core.trellis import TRELLIS_SCHEMA


@pytest.fixture
def con():
    """In-memory SQLite connection with trellis + step + file schemas and a root node."""
    c = connect(":memory:")
    c.executescript(TRELLIS_SCHEMA.format(application_id=0, schema_version=0))
    c.executescript(STEP_SCHEMA)
    c.executescript(FILE_SCHEMA)
    # available_resource is normally a temp table created by Scheduler.set_available_resources.
    c.execute(
        "CREATE TEMPORARY TABLE IF NOT EXISTS available_resource"
        " (name TEXT PRIMARY KEY, units INTEGER NOT NULL)"
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
    check_safe=False,
    need=Need.DEFAULT,
    implied_need=None,
    duration=1.0,
    tail_time=1.0,
    check_after=False,
    detached=False,
):
    """Insert a node row and a step row for a fictitious step."""
    if implied_need is None:
        implied_need = need
    con.execute(
        "INSERT INTO node (i, kind, label, creator, detached) VALUES (?, 'step', ?, ?, ?)",
        (node_id, f"echo {node_id}", creator_id, detached),
    )
    con.execute(
        "INSERT INTO step"
        " (node, state, need, duration, rescheduled_info, subshell,"
        " _safe, _check_safe, _implied_need, _tail_time, _check_after)"
        " VALUES (?, ?, ?, ?, '', 0, ?, ?, ?, ?, ?)",
        (
            node_id,
            state.value,
            need.value,
            duration,
            safe,
            check_safe,
            implied_need.value,
            tail_time,
            check_after,
        ),
    )


def _insert_file(con, node_id, creator_id):
    """Insert an intermediate file-type node used to route dependencies between steps.

    No row in the file table is needed: the _check_after queries only use the node and
    dependency tables.
    """
    con.execute(
        "INSERT INTO node (i, kind, label, creator, detached) VALUES (?, 'file', ?, ?, 0)",
        (node_id, f"file_{node_id}.txt", creator_id),
    )


def _add_dep(con, supplier_id, consumer_id):
    """Add a directed dependency edge from supplier to consumer."""
    con.execute(
        "INSERT INTO dependency (supplier, consumer) VALUES (?, ?)",
        (supplier_id, consumer_id),
    )


def _insert_input_file(con, node_id, creator_id, state, *, detached=False):
    """Insert a file node + file table row for use as a step input."""
    con.execute(
        "INSERT INTO node (i, kind, label, creator, detached) VALUES (?, 'file', ?, ?, ?)",
        (node_id, f"file_{node_id}.txt", creator_id, detached),
    )
    if state in (FileState.MISSING, FileState.AWAITED, FileState.VOLATILE):
        digest, mode, mtime, size, inode = b"\x75", 0, 0.0, 0, 0
    else:  # STATIC, BUILT, OUTDATED
        digest, mode, mtime, size, inode = b"\x01\x02\x03", 0o100644, 1000.0, 100, 42
    con.execute(
        "INSERT INTO file (node, state, digest, mode, mtime, size, inode)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (node_id, state.value, digest, mode, mtime, size, inode),
    )


def _add_dep_returning_id(con, supplier_id, consumer_id):
    """Add a dependency edge and return its primary-key id."""
    cur = con.execute(
        "INSERT INTO dependency (supplier, consumer) VALUES (?, ?)",
        (supplier_id, consumer_id),
    )
    return cur.lastrowid


def _mark_dep_amended(con, dep_id):
    """Mark an existing dependency as amended."""
    con.execute("INSERT INTO amended_dep (i) VALUES (?)", (dep_id,))


def _get_runnable_ids(con):
    """Run SELECT_RUNNABLE_STEPS and return the list of node ids in result order."""
    return [row[0] for row in con.execute(SELECT_RUNNABLE_STEPS).fetchall()]


def _get_safe(con):
    """Return a dict mapping step node id -> _safe value."""
    return dict(con.execute("SELECT node, _safe FROM step").fetchall())


def _run_update_meta_after(con):
    """Run the full update_meta_after logic against a bare SQLite connection."""
    con.execute(INIT_CHECK_AFTER)
    con.execute(EMPTY_CHECK_AFTER)
    con.execute(PRUNE_DETACHED_CHECK_AFTER)
    con.execute(PRUNE_REDUNDANT_CHECK_AFTER)
    ncheck = con.execute("SELECT COUNT(*) FROM check_after").fetchone()[0]
    first = True
    while ncheck > 0:
        con.execute(DROP_UPDATE_CHECK_AFTER)
        con.execute(INIT_UPDATE_CHECK_AFTER)
        con.execute(SELECT_UPDATE_CHECK_AFTER, {"first": first})
        con.execute(APPLY_UPDATE_CHECK_AFTER)
        con.execute(EMPTY_CHECK_AFTER)
        con.execute(PROPAGATE_UPDATE_CHECK_AFTER)
        ncheck = con.execute("SELECT COUNT(*) FROM check_after").fetchone()[0]
        first = False
    con.execute(DROP_CHECK_AFTER)
    con.execute("UPDATE step SET _check_after = 0 WHERE _check_after = 1")


# -----------------------------------------------------------------------
# Tests for RECURSIVE_UPDATE_SAFE
# -----------------------------------------------------------------------


def test_running_creator_makes_product_safe(con):
    """Product of a RUNNING step gets _safe=1 after the update."""
    _insert_step(con, 2, 1, StepState.RUNNING, check_safe=True)
    _insert_step(con, 3, 2, StepState.PENDING)
    con.execute(RECURSIVE_UPDATE_SAFE)
    assert _get_safe(con)[3] == 1


def test_succeeded_creator_makes_product_safe(con):
    """Product of a SUCCEEDED step gets _safe=1 after the update."""
    _insert_step(con, 2, 1, StepState.SUCCEEDED, check_safe=True)
    _insert_step(con, 3, 2, StepState.PENDING)
    con.execute(RECURSIVE_UPDATE_SAFE)
    assert _get_safe(con)[3] == 1


def test_failed_creator_keeps_product_unsafe(con):
    """Product of a FAILED step keeps _safe=0 after the update."""
    _insert_step(con, 2, 1, StepState.FAILED, check_safe=True)
    _insert_step(con, 3, 2, StepState.PENDING)
    con.execute(RECURSIVE_UPDATE_SAFE)
    assert _get_safe(con)[3] == 0


def test_pending_creator_keeps_product_unsafe(con):
    """Product of a PENDING step keeps _safe=0 after the update."""
    _insert_step(con, 2, 1, StepState.PENDING, check_safe=True)
    _insert_step(con, 3, 2, StepState.PENDING)
    con.execute(RECURSIVE_UPDATE_SAFE)
    assert _get_safe(con)[3] == 0


def test_no_check_safe_skips_update(con):
    """When no step has _check_safe=1, no _safe values are updated."""
    _insert_step(con, 2, 1, StepState.RUNNING, check_safe=False)
    _insert_step(con, 3, 2, StepState.PENDING, check_safe=False)
    con.execute(RECURSIVE_UPDATE_SAFE)
    assert _get_safe(con)[3] == 0


def test_deep_chain_propagates_safe(con):
    """Safety propagates through: root -> A(RUNNING) -> B(RUNNING) -> C(PENDING)."""
    _insert_step(con, 2, 1, StepState.RUNNING, check_safe=True)
    _insert_step(con, 3, 2, StepState.RUNNING)
    _insert_step(con, 4, 3, StepState.PENDING)
    con.execute(RECURSIVE_UPDATE_SAFE)
    safe = _get_safe(con)
    assert safe[3] == 1  # B is safe: creator A is RUNNING
    assert safe[4] == 1  # C is safe: creator B is RUNNING


def test_failed_intermediate_blocks_grandchild(con):
    """RUNNING A -> FAILED B -> PENDING C: B._safe=1 (can run) but C._safe=0 (blocked)."""
    _insert_step(con, 2, 1, StepState.RUNNING, check_safe=True)
    _insert_step(con, 3, 2, StepState.FAILED)
    _insert_step(con, 4, 3, StepState.PENDING)
    con.execute(RECURSIVE_UPDATE_SAFE)
    safe = _get_safe(con)
    assert safe[3] == 1  # B can still be queued: creator A is RUNNING
    assert safe[4] == 0  # C is blocked: creator B has not succeeded


def test_previously_safe_step_becomes_unsafe(con):
    """A step that was _safe=1 gets reset to 0 when its creator transitions to FAILED."""
    _insert_step(con, 2, 1, StepState.FAILED, check_safe=True, safe=True)
    _insert_step(con, 3, 2, StepState.PENDING, safe=True)
    con.execute(RECURSIVE_UPDATE_SAFE)
    assert _get_safe(con)[3] == 0


# -----------------------------------------------------------------------
# Tests for update_meta_after (INIT/SELECT/APPLY_UPDATE_CHECK_AFTER)
#
# Dependencies follow the two-hop pattern:  step_A -> file -> step_B
# meaning: dep(supplier=step_A, consumer=file) + dep(supplier=file, consumer=step_B).
# The queries compute _tail_time and _implied_need by traversing these two-hop paths.
# -----------------------------------------------------------------------


def test_isolated_step_tail_time_set_to_duration(con):
    """A step with no file consumers gets _tail_time = duration after the update."""
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
    downstream consumer of A.  B is processed first; propagation from B then queues A,
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


def test_implied_need_propagates_from_consumer(con):
    """A step's _implied_need is raised to the maximum _implied_need of its consumers."""
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


def test_tail_time_is_maximum_over_parallel_consumers(con):
    """When a step supplies to two independent consumers, _tail_time tracks the longer branch."""
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


def test_no_check_after_skips_update(con):
    """When no step has _check_after=1, no metadata values are updated."""
    _insert_step(con, 2, 1, StepState.PENDING, duration=3.0, tail_time=1.0, check_after=False)
    _run_update_meta_after(con)
    row = con.execute("SELECT _tail_time FROM step WHERE node = 2").fetchone()
    assert row[0] == pytest.approx(1.0)  # unchanged


# -----------------------------------------------------------------------
# Tests for PRUNE_REDUNDANT_CHECK_AFTER
# -----------------------------------------------------------------------


def _setup_check_after(con, *step_ids):
    """Create (if needed) and populate check_after with the given step ids."""
    con.execute(INIT_CHECK_AFTER)
    con.execute(EMPTY_CHECK_AFTER)
    for step_id in step_ids:
        con.execute("INSERT INTO check_after (i) VALUES (?)", (step_id,))


def _get_check_after_ids(con):
    """Return the sorted list of step ids currently in check_after."""
    return sorted(row[0] for row in con.execute("SELECT i FROM check_after").fetchall())


def test_prune_redundant_single_step_unchanged(con):
    """A single step in check_after with no dependencies is not pruned."""
    _insert_step(con, 2, 1, StepState.PENDING, check_after=True)
    _setup_check_after(con, 2)
    con.execute(PRUNE_REDUNDANT_CHECK_AFTER)
    assert _get_check_after_ids(con) == [2]


def test_prune_redundant_upstream_pruned_when_both_flagged(con):
    """Upstream A is pruned when both A and its downstream B are in check_after.

    Processing B will propagate to A via PROPAGATE_UPDATE_CHECK_AFTER, so A is redundant.
    """
    _insert_step(con, 2, 1, StepState.PENDING, check_after=True)  # A (upstream)
    _insert_file(con, 3, 1)
    _insert_step(con, 4, 1, StepState.PENDING, check_after=True)  # B (downstream)
    _add_dep(con, 2, 3)  # A -> file
    _add_dep(con, 3, 4)  # file -> B
    _setup_check_after(con, 2, 4)
    con.execute(PRUNE_REDUNDANT_CHECK_AFTER)
    assert _get_check_after_ids(con) == [4]  # A pruned, B stays


def test_prune_redundant_only_upstream_flagged_unchanged(con):
    """A is not pruned when only A (and not its downstream B) is in check_after."""
    _insert_step(con, 2, 1, StepState.PENDING, check_after=True)
    _insert_file(con, 3, 1)
    _insert_step(con, 4, 1, StepState.PENDING, check_after=False)
    _add_dep(con, 2, 3)
    _add_dep(con, 3, 4)
    _setup_check_after(con, 2)
    con.execute(PRUNE_REDUNDANT_CHECK_AFTER)
    assert _get_check_after_ids(con) == [2]


def test_prune_redundant_only_downstream_flagged_unchanged(con):
    """B is not pruned when only B (and not its upstream A) is in check_after."""
    _insert_step(con, 2, 1, StepState.PENDING, check_after=False)
    _insert_file(con, 3, 1)
    _insert_step(con, 4, 1, StepState.PENDING, check_after=True)
    _add_dep(con, 2, 3)
    _add_dep(con, 3, 4)
    _setup_check_after(con, 4)
    con.execute(PRUNE_REDUNDANT_CHECK_AFTER)
    assert _get_check_after_ids(con) == [4]


def test_prune_redundant_three_step_chain_keeps_only_tail(con):
    """In chain A -> B -> C with all three flagged, A and B are pruned; only C remains.

    C is the most-downstream leaf.  Processing C propagates to B, then B to A.
    """
    _insert_step(con, 2, 1, StepState.PENDING, check_after=True)  # A
    _insert_file(con, 3, 1)
    _insert_step(con, 4, 1, StepState.PENDING, check_after=True)  # B
    _insert_file(con, 5, 1)
    _insert_step(con, 6, 1, StepState.PENDING, check_after=True)  # C
    _add_dep(con, 2, 3)
    _add_dep(con, 3, 4)
    _add_dep(con, 4, 5)
    _add_dep(con, 5, 6)
    _setup_check_after(con, 2, 4, 6)
    con.execute(PRUNE_REDUNDANT_CHECK_AFTER)
    assert _get_check_after_ids(con) == [6]  # only C remains


def test_prune_redundant_parallel_consumers_prunes_shared_supplier(con):
    """A supplies both B and C; with all three flagged, A is pruned but B and C stay."""
    _insert_step(con, 2, 1, StepState.PENDING, check_after=True)  # A
    _insert_file(con, 3, 1)
    _insert_file(con, 4, 1)
    _insert_step(con, 5, 1, StepState.PENDING, check_after=True)  # B
    _insert_step(con, 6, 1, StepState.PENDING, check_after=True)  # C
    _add_dep(con, 2, 3)
    _add_dep(con, 3, 5)
    _add_dep(con, 2, 4)
    _add_dep(con, 4, 6)
    _setup_check_after(con, 2, 5, 6)
    con.execute(PRUNE_REDUNDANT_CHECK_AFTER)
    assert _get_check_after_ids(con) == [5, 6]  # B and C stay, A pruned


def test_prune_redundant_unconnected_steps_unchanged(con):
    """Steps with no dependency between them are never pruned."""
    _insert_step(con, 2, 1, StepState.PENDING, check_after=True)
    _insert_step(con, 3, 1, StepState.PENDING, check_after=True)
    _setup_check_after(con, 2, 3)
    con.execute(PRUNE_REDUNDANT_CHECK_AFTER)
    assert _get_check_after_ids(con) == [2, 3]


def test_prune_redundant_detached_intermediate_breaks_chain(con):
    """A detached step in the middle of a chain blocks supplier traversal.

    With A -> file -> B(detached) -> file -> C and A and C both in check_after,
    the traversal from C stops at the detached B, so A is NOT pruned.
    """
    _insert_step(con, 2, 1, StepState.PENDING, check_after=True)  # A
    _insert_file(con, 3, 1)
    _insert_step(con, 4, 1, StepState.PENDING, check_after=True, detached=True)  # B (detached)
    _insert_file(con, 5, 1)
    _insert_step(con, 6, 1, StepState.PENDING, check_after=True)  # C
    _add_dep(con, 2, 3)
    _add_dep(con, 3, 4)
    _add_dep(con, 4, 5)
    _add_dep(con, 5, 6)
    _setup_check_after(con, 2, 6)  # B is detached so not in check_after
    con.execute(PRUNE_REDUNDANT_CHECK_AFTER)
    assert _get_check_after_ids(con) == [2, 6]  # A not pruned: chain broken by detached B


def test_prune_redundant_stops_at_first_hit(con):
    """Traversal stops at the first check_after step it finds upstream.

    With B -> C both in check_after and A upstream of B (not in check_after),
    the trace from C finds B (hit=True, stops) and never reaches A.
    B is pruned; A is unaffected because it is not in check_after.
    """
    _insert_step(con, 2, 1, StepState.PENDING, check_after=False)  # A (not flagged)
    _insert_file(con, 3, 1)
    _insert_step(con, 4, 1, StepState.PENDING, check_after=True)  # B
    _insert_file(con, 5, 1)
    _insert_step(con, 6, 1, StepState.PENDING, check_after=True)  # C
    _add_dep(con, 2, 3)
    _add_dep(con, 3, 4)
    _add_dep(con, 4, 5)
    _add_dep(con, 5, 6)
    _setup_check_after(con, 4, 6)
    con.execute(PRUNE_REDUNDANT_CHECK_AFTER)
    assert _get_check_after_ids(con) == [6]  # B pruned; C stays


# -----------------------------------------------------------------------
# Tests for SELECT_RUNNABLE_STEPS
# -----------------------------------------------------------------------


def test_runnable_step_with_no_inputs(con):
    """A PENDING, safe, non-detached DEFAULT step with no file inputs is returned."""
    _insert_step(con, 2, 1, StepState.PENDING, safe=True, implied_need=Need.DEFAULT)
    assert _get_runnable_ids(con) == [2]


def test_running_step_not_runnable(con):
    """A RUNNING step is excluded — only PENDING steps are candidates."""
    _insert_step(con, 2, 1, StepState.RUNNING, safe=True, implied_need=Need.DEFAULT)
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


def test_rescheduled_step_not_runnable(con):
    """A PENDING step with non-empty rescheduled_info is excluded."""
    _insert_step(con, 2, 1, StepState.PENDING, safe=True, implied_need=Need.DEFAULT)
    con.execute("UPDATE step SET rescheduled_info = 'some reason' WHERE node = 2")
    assert _get_runnable_ids(con) == []


# -- input file blocking -------------------------------------------------


def test_volatile_input_blocks_step(con):
    """A VOLATILE input always blocks a step, regardless of initial/amended status."""
    _insert_step(con, 2, 1, StepState.PENDING, safe=True, implied_need=Need.DEFAULT)
    _insert_input_file(con, 3, 1, FileState.VOLATILE)
    _add_dep(con, 3, 2)
    assert _get_runnable_ids(con) == []


def test_initial_input_awaited_blocks_step(con):
    """An initial dependency on an AWAITED file blocks the step."""
    _insert_step(con, 2, 1, StepState.PENDING, safe=True, implied_need=Need.DEFAULT)
    _insert_input_file(con, 3, 1, FileState.AWAITED)
    _add_dep(con, 3, 2)
    assert _get_runnable_ids(con) == []


def test_initial_input_outdated_blocks_step(con):
    """An initial dependency on an OUTDATED file blocks the step."""
    _insert_step(con, 2, 1, StepState.PENDING, safe=True, implied_need=Need.DEFAULT)
    _insert_input_file(con, 3, 1, FileState.OUTDATED)
    _add_dep(con, 3, 2)
    assert _get_runnable_ids(con) == []


def test_initial_input_missing_blocks_step(con):
    """An initial dependency on a MISSING file blocks the step."""
    _insert_step(con, 2, 1, StepState.PENDING, safe=True, implied_need=Need.DEFAULT)
    _insert_input_file(con, 3, 1, FileState.MISSING)
    _add_dep(con, 3, 2)
    assert _get_runnable_ids(con) == []


def test_initial_input_detached_node_blocks_step(con):
    """An initial dependency on a detached file node blocks the step, regardless of file state."""
    _insert_step(con, 2, 1, StepState.PENDING, safe=True, implied_need=Need.DEFAULT)
    _insert_input_file(con, 3, 1, FileState.BUILT, detached=True)
    _add_dep(con, 3, 2)
    assert _get_runnable_ids(con) == []


def test_initial_input_built_allows_step(con):
    """An initial dependency on a BUILT file does not block the step."""
    _insert_step(con, 2, 1, StepState.PENDING, safe=True, implied_need=Need.DEFAULT)
    _insert_input_file(con, 3, 1, FileState.BUILT)
    _add_dep(con, 3, 2)
    assert _get_runnable_ids(con) == [2]


def test_initial_input_static_allows_step(con):
    """An initial dependency on a STATIC file does not block the step."""
    _insert_step(con, 2, 1, StepState.PENDING, safe=True, implied_need=Need.DEFAULT)
    _insert_input_file(con, 3, 1, FileState.STATIC)
    _add_dep(con, 3, 2)
    assert _get_runnable_ids(con) == [2]


def test_amended_input_awaited_blocks_step(con):
    """An amended, attached input in AWAITED state blocks the step."""
    _insert_step(con, 2, 1, StepState.PENDING, safe=True, implied_need=Need.DEFAULT)
    _insert_input_file(con, 3, 1, FileState.AWAITED)
    dep_id = _add_dep_returning_id(con, 3, 2)
    _mark_dep_amended(con, dep_id)
    assert _get_runnable_ids(con) == []


def test_amended_input_outdated_blocks_step(con):
    """An amended, attached input in OUTDATED state blocks the step."""
    _insert_step(con, 2, 1, StepState.PENDING, safe=True, implied_need=Need.DEFAULT)
    _insert_input_file(con, 3, 1, FileState.OUTDATED)
    dep_id = _add_dep_returning_id(con, 3, 2)
    _mark_dep_amended(con, dep_id)
    assert _get_runnable_ids(con) == []


def test_amended_input_built_allows_step(con):
    """An amended, attached input in BUILT state does not block the step."""
    _insert_step(con, 2, 1, StepState.PENDING, safe=True, implied_need=Need.DEFAULT)
    _insert_input_file(con, 3, 1, FileState.BUILT)
    dep_id = _add_dep_returning_id(con, 3, 2)
    _mark_dep_amended(con, dep_id)
    assert _get_runnable_ids(con) == [2]


def test_amended_input_missing_allows_step(con):
    """An amended, attached input in MISSING state does not block the step.

    MISSING is neither AWAITED nor OUTDATED, so case 1 of the blocking condition does not apply.
    The step proceeds and will validate its amended inputs before running.
    """
    _insert_step(con, 2, 1, StepState.PENDING, safe=True, implied_need=Need.DEFAULT)
    _insert_input_file(con, 3, 1, FileState.MISSING)
    dep_id = _add_dep_returning_id(con, 3, 2)
    _mark_dep_amended(con, dep_id)
    assert _get_runnable_ids(con) == [2]


def test_amended_input_detached_allows_step(con):
    """An amended dependency on a detached file node does not block the step.

    Case 1 requires NOT input_node.detached, and case 2 only covers initial (non-amended) deps,
    so a detached amended input is not a blocking condition.
    """
    _insert_step(con, 2, 1, StepState.PENDING, safe=True, implied_need=Need.DEFAULT)
    _insert_input_file(con, 3, 1, FileState.MISSING, detached=True)
    dep_id = _add_dep_returning_id(con, 3, 2)
    _mark_dep_amended(con, dep_id)
    assert _get_runnable_ids(con) == [2]


def test_one_ready_one_blocking_input_excludes_step(con):
    """When at least one input is blocking, the step is excluded even if others are ready."""
    _insert_step(con, 2, 1, StepState.PENDING, safe=True, implied_need=Need.DEFAULT)
    _insert_input_file(con, 3, 1, FileState.BUILT)  # ready
    _insert_input_file(con, 4, 1, FileState.AWAITED)  # blocking
    _add_dep(con, 3, 2)
    _add_dep(con, 4, 2)
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
    """PLAN steps are ordered before DEFAULT steps, regardless of tail_time."""
    _insert_step(
        con, 2, 1, StepState.PENDING, safe=True, implied_need=Need.DEFAULT, tail_time=100.0
    )
    _insert_step(con, 3, 1, StepState.PENDING, safe=True, implied_need=Need.PLAN, tail_time=1.0)
    ids = _get_runnable_ids(con)
    assert ids == [3, 2]  # PLAN first despite lower tail_time


def test_ordering_higher_tail_time_first(con):
    """Within the same implied_need level, steps with higher _tail_time come first."""
    _insert_step(con, 2, 1, StepState.PENDING, safe=True, implied_need=Need.DEFAULT, tail_time=5.0)
    _insert_step(con, 3, 1, StepState.PENDING, safe=True, implied_need=Need.DEFAULT, tail_time=10.0)
    ids = _get_runnable_ids(con)
    assert ids == [3, 2]


def test_ordering_label_tiebreaker(con):
    """When tail_time and implied_need are equal, steps are sorted alphabetically by label."""
    _insert_step(con, 2, 1, StepState.PENDING, safe=True, implied_need=Need.DEFAULT, tail_time=5.0)
    _insert_step(con, 3, 1, StepState.PENDING, safe=True, implied_need=Need.DEFAULT, tail_time=5.0)
    # _insert_step labels these "echo 2" and "echo 3"; "echo 2" < "echo 3" alphabetically.
    ids = _get_runnable_ids(con)
    assert ids == [2, 3]


# -----------------------------------------------------------------------
# Tests for SELECT_INPUTS
# -----------------------------------------------------------------------


def _get_inputs(con, consumer_id):
    """Run SELECT_INPUTS for the given consumer and return all rows as a list of dicts."""
    rows = con.execute(SELECT_INPUTS, (consumer_id,)).fetchall()
    keys = ("label", "detached", "state", "amended", "digest", "mode", "mtime", "size", "inode")
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
    assert row["digest"] == b"\x01\x02\x03"
    assert row["mode"] == 0o100644
    assert row["mtime"] == pytest.approx(1000.0)
    assert row["size"] == 100
    assert row["inode"] == 42


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
    assert rows[0]["digest"] == b"\x75"
    assert rows[0]["mode"] == 0
    assert rows[0]["mtime"] == pytest.approx(0.0)
    assert rows[0]["size"] == 0
    assert rows[0]["inode"] == 0


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


def test_select_inputs_only_returns_inputs_for_queried_consumer(con):
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
# Tests for SELECT_PENDING_REASONS
# -----------------------------------------------------------------------


def _get_pending_reasons(con):
    """Run SELECT_PENDING_REASONS and return a dict mapping node id -> row dict."""
    keys = ("i", "label", "safe", "rescheduled", "has_unavailable_inputs", "has_resource_issue")
    return {
        row[0]: dict(zip(keys, row, strict=True))
        for row in con.execute(SELECT_PENDING_REASONS).fetchall()
    }


def test_pending_reasons_basic_row(con):
    """A ready PENDING step returns a row with all flags cleared."""
    _insert_step(con, 2, 1, StepState.PENDING, safe=True, implied_need=Need.DEFAULT)
    rows = _get_pending_reasons(con)
    assert 2 in rows
    r = rows[2]
    assert r["label"] == "echo 2"
    assert r["safe"] == 1
    assert r["rescheduled"] == 0
    assert r["has_unavailable_inputs"] == 0
    assert r["has_resource_issue"] == 0


def test_pending_reasons_excludes_running(con):
    """RUNNING steps are not returned."""
    _insert_step(con, 2, 1, StepState.RUNNING)
    assert _get_pending_reasons(con) == {}


def test_pending_reasons_excludes_succeeded(con):
    """SUCCEEDED steps are not returned."""
    _insert_step(con, 2, 1, StepState.SUCCEEDED)
    assert _get_pending_reasons(con) == {}


def test_pending_reasons_excludes_failed(con):
    """FAILED steps are not returned."""
    _insert_step(con, 2, 1, StepState.FAILED)
    assert _get_pending_reasons(con) == {}


def test_pending_reasons_excludes_optional(con):
    """PENDING steps with implied_need=OPTIONAL are not returned."""
    _insert_step(con, 2, 1, StepState.PENDING, implied_need=Need.OPTIONAL)
    assert _get_pending_reasons(con) == {}


def test_pending_reasons_excludes_detached(con):
    """Detached PENDING steps are not returned."""
    _insert_step(con, 2, 1, StepState.PENDING, detached=True)
    assert _get_pending_reasons(con) == {}


def test_pending_reasons_safe_flag_false(con):
    """A PENDING step with _safe=0 returns safe=0."""
    _insert_step(con, 2, 1, StepState.PENDING, safe=False)
    rows = _get_pending_reasons(con)
    assert rows[2]["safe"] == 0


def test_pending_reasons_rescheduled_flag(con):
    """A step with non-empty rescheduled_info returns rescheduled=1."""
    _insert_step(con, 2, 1, StepState.PENDING, safe=True, implied_need=Need.DEFAULT)
    con.execute("UPDATE step SET rescheduled_info = 'some reason' WHERE node = 2")
    rows = _get_pending_reasons(con)
    assert rows[2]["rescheduled"] == 1


# -- has_unavailable_inputs ----------------------------------------------


def test_pending_reasons_volatile_input(con):
    """A VOLATILE input sets has_unavailable_inputs=1."""
    _insert_step(con, 2, 1, StepState.PENDING, safe=True)
    _insert_input_file(con, 3, 1, FileState.VOLATILE)
    _add_dep(con, 3, 2)
    assert _get_pending_reasons(con)[2]["has_unavailable_inputs"] == 1


def test_pending_reasons_initial_awaited_input(con):
    """An initial AWAITED input sets has_unavailable_inputs=1."""
    _insert_step(con, 2, 1, StepState.PENDING, safe=True)
    _insert_input_file(con, 3, 1, FileState.AWAITED)
    _add_dep(con, 3, 2)
    assert _get_pending_reasons(con)[2]["has_unavailable_inputs"] == 1


def test_pending_reasons_initial_outdated_input(con):
    """An initial OUTDATED input sets has_unavailable_inputs=1."""
    _insert_step(con, 2, 1, StepState.PENDING, safe=True)
    _insert_input_file(con, 3, 1, FileState.OUTDATED)
    _add_dep(con, 3, 2)
    assert _get_pending_reasons(con)[2]["has_unavailable_inputs"] == 1


def test_pending_reasons_initial_missing_input(con):
    """An initial MISSING input sets has_unavailable_inputs=1."""
    _insert_step(con, 2, 1, StepState.PENDING, safe=True)
    _insert_input_file(con, 3, 1, FileState.MISSING)
    _add_dep(con, 3, 2)
    assert _get_pending_reasons(con)[2]["has_unavailable_inputs"] == 1


def test_pending_reasons_initial_detached_input(con):
    """An initial dependency on a detached node sets has_unavailable_inputs=1."""
    _insert_step(con, 2, 1, StepState.PENDING, safe=True)
    _insert_input_file(con, 3, 1, FileState.BUILT, detached=True)
    _add_dep(con, 3, 2)
    assert _get_pending_reasons(con)[2]["has_unavailable_inputs"] == 1


def test_pending_reasons_initial_built_input(con):
    """An initial BUILT input does not set has_unavailable_inputs."""
    _insert_step(con, 2, 1, StepState.PENDING, safe=True)
    _insert_input_file(con, 3, 1, FileState.BUILT)
    _add_dep(con, 3, 2)
    assert _get_pending_reasons(con)[2]["has_unavailable_inputs"] == 0


def test_pending_reasons_initial_static_input(con):
    """An initial STATIC input does not set has_unavailable_inputs."""
    _insert_step(con, 2, 1, StepState.PENDING, safe=True)
    _insert_input_file(con, 3, 1, FileState.STATIC)
    _add_dep(con, 3, 2)
    assert _get_pending_reasons(con)[2]["has_unavailable_inputs"] == 0


def test_pending_reasons_amended_awaited_input(con):
    """An amended, non-detached AWAITED input sets has_unavailable_inputs=1."""
    _insert_step(con, 2, 1, StepState.PENDING, safe=True)
    _insert_input_file(con, 3, 1, FileState.AWAITED)
    dep_id = _add_dep_returning_id(con, 3, 2)
    _mark_dep_amended(con, dep_id)
    assert _get_pending_reasons(con)[2]["has_unavailable_inputs"] == 1


def test_pending_reasons_amended_outdated_input(con):
    """An amended, non-detached OUTDATED input sets has_unavailable_inputs=1."""
    _insert_step(con, 2, 1, StepState.PENDING, safe=True)
    _insert_input_file(con, 3, 1, FileState.OUTDATED)
    dep_id = _add_dep_returning_id(con, 3, 2)
    _mark_dep_amended(con, dep_id)
    assert _get_pending_reasons(con)[2]["has_unavailable_inputs"] == 1


def test_pending_reasons_amended_built_input(con):
    """An amended BUILT input does not set has_unavailable_inputs."""
    _insert_step(con, 2, 1, StepState.PENDING, safe=True)
    _insert_input_file(con, 3, 1, FileState.BUILT)
    dep_id = _add_dep_returning_id(con, 3, 2)
    _mark_dep_amended(con, dep_id)
    assert _get_pending_reasons(con)[2]["has_unavailable_inputs"] == 0


def test_pending_reasons_amended_missing_input(con):
    """An amended MISSING input does not set has_unavailable_inputs (step can validate itself)."""
    _insert_step(con, 2, 1, StepState.PENDING, safe=True)
    _insert_input_file(con, 3, 1, FileState.MISSING)
    dep_id = _add_dep_returning_id(con, 3, 2)
    _mark_dep_amended(con, dep_id)
    assert _get_pending_reasons(con)[2]["has_unavailable_inputs"] == 0


def test_pending_reasons_amended_detached_input(con):
    """An amended dependency on a detached node does not set has_unavailable_inputs."""
    _insert_step(con, 2, 1, StepState.PENDING, safe=True)
    _insert_input_file(con, 3, 1, FileState.MISSING, detached=True)
    dep_id = _add_dep_returning_id(con, 3, 2)
    _mark_dep_amended(con, dep_id)
    assert _get_pending_reasons(con)[2]["has_unavailable_inputs"] == 0


# -- has_resource_issue --------------------------------------------------


def test_pending_reasons_resource_undefined(con):
    """A required resource absent from available_resource sets has_resource_issue=1."""
    _insert_step(con, 2, 1, StepState.PENDING, safe=True)
    con.execute("INSERT INTO step_resource (node, name, units) VALUES (2, 'gpu', 1)")
    assert _get_pending_reasons(con)[2]["has_resource_issue"] == 1


def test_pending_reasons_resource_total_below_required(con):
    """Total available units strictly below required units sets has_resource_issue=1.

    Unlike SELECT_RUNNABLE_STEPS, this query compares total capacity against the requirement,
    not the remaining capacity.  A resource that is temporarily over-committed is not flagged.
    """
    _insert_step(con, 2, 1, StepState.PENDING, safe=True)
    con.execute("INSERT INTO available_resource (name, units) VALUES ('gpu', 1)")
    con.execute("INSERT INTO step_resource (node, name, units) VALUES (2, 'gpu', 2)")
    assert _get_pending_reasons(con)[2]["has_resource_issue"] == 1


def test_pending_reasons_resource_available(con):
    """A fitting resource requirement does not set has_resource_issue."""
    _insert_step(con, 2, 1, StepState.PENDING, safe=True)
    con.execute("INSERT INTO available_resource (name, units) VALUES ('gpu', 4)")
    con.execute("INSERT INTO step_resource (node, name, units) VALUES (2, 'gpu', 1)")
    assert _get_pending_reasons(con)[2]["has_resource_issue"] == 0


# -- ordering ------------------------------------------------------------


def test_pending_reasons_ordering_plan_before_default(con):
    """PLAN steps appear before DEFAULT steps regardless of tail_time."""
    _insert_step(
        con, 2, 1, StepState.PENDING, safe=True, implied_need=Need.DEFAULT, tail_time=100.0
    )
    _insert_step(con, 3, 1, StepState.PENDING, safe=True, implied_need=Need.PLAN, tail_time=1.0)
    ids = [r["i"] for r in _get_pending_reasons(con).values()]
    assert ids == [3, 2]


def test_pending_reasons_ordering_higher_tail_time_first(con):
    """Within the same implied_need level, higher _tail_time comes first."""
    _insert_step(con, 2, 1, StepState.PENDING, safe=True, implied_need=Need.DEFAULT, tail_time=5.0)
    _insert_step(con, 3, 1, StepState.PENDING, safe=True, implied_need=Need.DEFAULT, tail_time=10.0)
    ids = [r["i"] for r in _get_pending_reasons(con).values()]
    assert ids == [3, 2]


def test_pending_reasons_ordering_label_tiebreaker(con):
    """Equal tail_time and implied_need: alphabetical label order."""
    _insert_step(con, 2, 1, StepState.PENDING, safe=True, implied_need=Need.DEFAULT, tail_time=5.0)
    _insert_step(con, 3, 1, StepState.PENDING, safe=True, implied_need=Need.DEFAULT, tail_time=5.0)
    # labels are "echo 2" and "echo 3"; "echo 2" < "echo 3"
    ids = [r["i"] for r in _get_pending_reasons(con).values()]
    assert ids == [2, 3]


# -----------------------------------------------------------------------
# Tests for UNAVAILABLE_INPUT
#
# UNAVAILABLE_INPUT is a correlated subquery parameterized by `node.i` from
# the outer query.  The helper below wraps it with
# `SELECT EXISTS (...) FROM node WHERE node.i = ?` so each test can drive it
# directly against a known consumer step.
#
# The three blocking branches are:
#   VOLATILE      – any dep type, any detach state
#   Case 1        – amended AND NOT detached AND state IN (AWAITED, OUTDATED)
#   Case 2        – initial (not amended) AND (detached OR state not in {BUILT, STATIC})
# -----------------------------------------------------------------------


def _has_unavailable_input(con, consumer_id):
    """Return whether the given consumer has at least one unavailable input."""
    row = con.execute(
        f"SELECT EXISTS ({UNAVAILABLE_INPUT}) FROM node WHERE node.i = ?",
        (consumer_id,),
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
# Tests for CHECKING state: RECURSIVE_UPDATE_SAFE
# -----------------------------------------------------------------------


def test_checking_creator_makes_product_safe(con):
    """A product of a CHECKING step gets _safe=1 (CHECKING is a safe running state)."""
    _insert_step(con, 2, 1, StepState.CHECKING, check_safe=True)
    _insert_step(con, 3, 2, StepState.PENDING)
    con.execute(RECURSIVE_UPDATE_SAFE)
    assert _get_safe(con)[3] == 1


def test_checking_in_chain_propagates_safe(con):
    """Safety propagates through: root -> A(CHECKING) -> B(PENDING)."""
    _insert_step(con, 2, 1, StepState.CHECKING, check_safe=True)
    _insert_step(con, 3, 2, StepState.PENDING)
    con.execute(RECURSIVE_UPDATE_SAFE)
    assert _get_safe(con)[3] == 1


# -----------------------------------------------------------------------
# Tests for SELECT_CHECKABLE_STEPS and UNAVAILABLE_INPUT
# -----------------------------------------------------------------------


def _insert_step_hash(con, node_id):
    """Insert a minimal step_hash row so the step is considered checkable."""
    con.execute(
        "INSERT INTO step_hash (node, inp_digest, inp_info, out_digest, out_info)"
        " VALUES (?, X'aabbcc', NULL, X'ddeeff', NULL)",
        (node_id,),
    )


def _get_checkable_ids(con):
    """Run SELECT_CHECKABLE_STEPS and return the list of node ids in result order."""
    return [row[0] for row in con.execute(SELECT_CHECKABLE_STEPS).fetchall()]


def _has_unavailable_input(con, consumer_id):
    """Return whether the given consumer has at least one unavailable input."""
    row = con.execute(
        f"SELECT EXISTS ({UNAVAILABLE_INPUT}) FROM node WHERE node.i = ?",
        (consumer_id,),
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


def test_checkable_step_blocked_by_unavailable_initial_input(con):
    """A step with a hash but an unavailable initial (non-amended) input is NOT checkable."""
    _insert_step(con, 2, 1, StepState.PENDING, safe=True, implied_need=Need.DEFAULT)
    _insert_step_hash(con, 2)
    _insert_input_file(con, 3, 1, FileState.AWAITED)
    _add_dep(con, 3, 2)
    assert _get_checkable_ids(con) == []


def test_checkable_step_with_ready_initial_and_unready_amended_input(con):
    """A step with a hash: ready initial input + unready amended input IS checkable.

    This is the ValidateAmendedJob case: amended inputs not yet ready, but we can
    still validate that the initial inputs haven't changed (without resource slots).
    """
    _insert_step(con, 2, 1, StepState.PENDING, safe=True, implied_need=Need.DEFAULT)
    _insert_step_hash(con, 2)
    # Ready initial input
    _insert_input_file(con, 3, 1, FileState.STATIC)
    _add_dep(con, 3, 2)
    # Unready amended input (MISSING — case 1 of UNAVAILABLE_INPUT blocks but not INITIAL)
    _insert_input_file(con, 4, 1, FileState.MISSING)
    dep_id = _add_dep_returning_id(con, 4, 2)
    _mark_dep_amended(con, dep_id)
    # MISSING amended inputs are NOT blocked by UNAVAILABLE_INPUT (case 1 only blocks
    # AWAITED/OUTDATED), so both SELECT_RUNNABLE_STEPS and SELECT_CHECKABLE_STEPS allow them.
    assert _get_checkable_ids(con) == [2]


def test_checkable_step_with_hash_and_missing_resource(con):
    """A step with a hash is checkable even when its resource is NOT available.

    This is the core property: SELECT_CHECKABLE_STEPS does not check resource availability,
    so PENDING steps with hashes are scheduled for CHECKING without waiting for resources.
    This ensures skipping is never blocked by named resource restrictions.
    """
    _insert_step(con, 2, 1, StepState.PENDING, safe=True, implied_need=Need.DEFAULT)
    _insert_step_hash(con, 2)
    con.execute("INSERT INTO step_resource (node, name, units) VALUES (2, 'gpu', 1)")
    # No row in available_resource → resource is undefined → SELECT_RUNNABLE_STEPS would block
    assert _get_runnable_ids(con) == []
    # But SELECT_CHECKABLE_STEPS ignores resources → step is still checkable
    assert _get_checkable_ids(con) == [2]


def test_checkable_step_with_hash_and_exhausted_resource(con):
    """A step with a hash is checkable even when its resource pool is fully consumed."""
    _insert_step(con, 2, 1, StepState.PENDING, safe=True, implied_need=Need.DEFAULT)
    _insert_step_hash(con, 2)
    _insert_step(con, 3, 1, StepState.RUNNING, safe=True, implied_need=Need.DEFAULT)
    con.execute("INSERT INTO available_resource (name, units) VALUES ('gpu', 1)")
    con.execute("INSERT INTO step_resource (node, name, units) VALUES (2, 'gpu', 1)")
    con.execute("INSERT INTO step_resource (node, name, units) VALUES (3, 'gpu', 1)")
    # SELECT_RUNNABLE_STEPS would block: available(1) - running(1) = 0 < required(1)
    assert _get_runnable_ids(con) == []
    # But SELECT_CHECKABLE_STEPS ignores resources → step is checkable
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
