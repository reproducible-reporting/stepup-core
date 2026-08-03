# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Unit tests for stepup.core.step."""

import sqlite3
from types import SimpleNamespace

import pytest

from stepup.core.enums import Need, StepState
from stepup.core.exceptions import GraphError
from stepup.core.file import FILE_SCHEMA
from stepup.core.sqlite3 import connect
from stepup.core.step import RECURSIVE_CHECK_WITH_PRODUCTS, STEP_SCHEMA, Step, truncate_output
from stepup.core.trellis import TRELLIS_SCHEMA


@pytest.fixture
def con():
    """In-memory SQLite connection with trellis + step + file schemas and a root node."""
    c = connect(":memory:")
    c.executescript(TRELLIS_SCHEMA.format(application_id=0, schema_version=0))
    # FILE_SCHEMA must load before STEP_SCHEMA: STEP_SCHEMA's step_file_check_ready_*
    # triggers are declared ON file, which requires the file table to already exist.
    c.executescript(FILE_SCHEMA)
    c.executescript(STEP_SCHEMA)
    # Root node has a self-referential creator.
    c.execute("INSERT INTO node (i, kind, label, creator, detached) VALUES (1, 'root', '', 1, 0)")
    return c


def _insert_step(con, node_id, creator_id):
    """Insert a node row and a step row with both _check_* flags cleared."""
    con.execute(
        "INSERT INTO node (i, kind, label, creator, detached) VALUES (?, 'step', ?, ?, 0)",
        (node_id, f"echo {node_id}", creator_id),
    )
    con.execute(
        "INSERT INTO step"
        " (node, state, need, duration, deferred, defer_count,"
        " shell, _safe, _check_safe, _implied_need, _tail_time, _check_after)"
        " VALUES (?, ?, ?, 1.0, 0, 0, 0, 0, 0, ?, 1.0, 0)",
        (node_id, StepState.PENDING.value, Need.DEFAULT.value, Need.DEFAULT.value),
    )


def _make_step(con, node_id):
    """Wrap an already-inserted step row in a `Step` object usable outside a full `Workflow`.

    `Step.db` only ever needs `self.graph.db`, so a bare namespace exposing `con` as `db`
    is enough to call instance methods without constructing a real `Trellis`/`Workflow`.
    """
    return Step(graph=SimpleNamespace(db=con), i=node_id, label=f"echo {node_id}")


def _flagged(con):
    """Return the set of step node ids whose _check_safe and _check_after are both set."""
    flagged = set()
    for node, check_safe, check_after in con.execute(
        "SELECT node, _check_safe, _check_after FROM step"
    ):
        # The two flags are always set together by RECURSIVE_CHECK_WITH_PRODUCTS.
        assert check_safe == check_after
        if check_safe:
            flagged.add(node)
    return flagged


def test_flag_checks_with_products_flags_step_and_all_products(con):
    """The flagged step and its (recursive) product steps must be flagged."""
    # Creator chain: root(1) -> A(2) -> B(3) -> C(4)
    _insert_step(con, 2, 1)
    _insert_step(con, 3, 2)
    _insert_step(con, 4, 3)
    con.execute(RECURSIVE_CHECK_WITH_PRODUCTS, (2,))
    assert _flagged(con) == {2, 3, 4}


def test_flag_checks_with_products_does_not_flag_sibling_subtree(con):
    """Steps in an unrelated creator subtree must not be flagged."""
    # Two independent subtrees under root:
    #   root(1) -> A(2) -> B(3)
    #   root(1) -> D(4) -> E(5)
    _insert_step(con, 2, 1)
    _insert_step(con, 3, 2)
    _insert_step(con, 4, 1)
    _insert_step(con, 5, 4)
    con.execute(RECURSIVE_CHECK_WITH_PRODUCTS, (2,))
    # Only A and its product B may be flagged, not the sibling subtree D, E.
    assert _flagged(con) == {2, 3}


def test_flag_checks_with_products_leaf_flags_only_itself(con):
    """A leaf step (one that creates nothing) must flag only itself."""
    # root(1) -> A(2) -> B(3); flag the leaf B, which creates no products.
    _insert_step(con, 2, 1)
    _insert_step(con, 3, 2)
    con.execute(RECURSIVE_CHECK_WITH_PRODUCTS, (3,))
    # The flag must not propagate "upward" to the creator A.
    assert _flagged(con) == {3}


def test_flag_checks_with_products_single_step(con):
    """Flagging the only step flags exactly that step."""
    _insert_step(con, 2, 1)
    con.execute(RECURSIVE_CHECK_WITH_PRODUCTS, (2,))
    assert _flagged(con) == {2}


def test_hold_sets_holding_and_flags_products(con):
    """`hold()` sets `_holding` and flags `_check_safe` on the step and its products."""
    # root(1) -> A(2) -> B(3)
    _insert_step(con, 2, 1)
    _insert_step(con, 3, 2)
    step_a = _make_step(con, 2)
    step_a.hold()
    assert step_a.is_holding() is True
    assert _flagged(con) == {2, 3}


def test_release_clears_holding_and_flags_products(con):
    """`release()` clears `_holding` and flags `_check_safe` on the step and its products."""
    # root(1) -> A(2) -> B(3)
    _insert_step(con, 2, 1)
    _insert_step(con, 3, 2)
    step_a = _make_step(con, 2)
    step_a.hold()
    con.execute("UPDATE step SET _check_safe = 0")  # simulate the scheduler having caught up
    step_a.release()
    assert step_a.is_holding() is False
    assert _flagged(con) == {2, 3}


def test_hold_nested_stays_holding_until_outer_release(con):
    """Nested `hold()` calls on the same step compose: only the outermost `release()` clears
    `_holding`, matching the re-entrant design (a bare counter, no enter/exit order checking).
    """
    _insert_step(con, 2, 1)
    step_a = _make_step(con, 2)
    step_a.hold()
    step_a.hold()
    assert step_a.is_holding() is True
    step_a.release()
    # Still holding: only one of the two hold() calls has been matched so far.
    assert step_a.is_holding() is True
    step_a.release()
    assert step_a.is_holding() is False


def test_hold_release_nested_does_not_reflag_products(con):
    """A nested `hold()`/`release()` pair on an already-holding step does not re-flag products.

    Only the 0 -> 1 and 1 -> 0 transitions of `_holding` can change any descendant's
    dispatch eligibility, so a 1 -> 2 -> 1 transition must not trigger another subtree scan.
    """
    # root(1) -> A(2) -> B(3)
    _insert_step(con, 2, 1)
    _insert_step(con, 3, 2)
    step_a = _make_step(con, 2)
    step_a.hold()
    con.execute("UPDATE step SET _check_safe = 0, _check_after = 0")  # scheduler caught up
    step_a.hold()  # 1 -> 2: no new blocking, must not re-flag
    assert _flagged(con) == set()
    step_a.release()  # 2 -> 1: still holding, must not re-flag
    assert _flagged(con) == set()
    assert step_a.is_holding() is True
    step_a.release()  # 1 -> 0: newly unblocks, must flag
    assert _flagged(con) == {2, 3}


def test_state_update_away_from_running_resets_holding_via_set_state(con):
    """Setting `state` away from `RUNNING` through `Step.set_state()` clears `_holding`."""
    _insert_step(con, 2, 1)
    step_a = _make_step(con, 2)
    con.execute("UPDATE step SET state = ? WHERE node = ?", (StepState.RUNNING.value, 2))
    step_a.hold()
    assert step_a.is_holding() is True
    step_a.set_state(StepState.FAILED)
    assert step_a.is_holding() is False


def test_raw_sql_state_update_away_from_running_resets_holding(con):
    """The trigger fires on a raw SQL `UPDATE`, not just calls through `Step.set_state()`.

    This mirrors `startup.py`'s crash-recovery reset, which updates `state` directly.
    """
    _insert_step(con, 2, 1)
    step_a = _make_step(con, 2)
    con.execute("UPDATE step SET state = ? WHERE node = ?", (StepState.RUNNING.value, 2))
    step_a.hold()
    assert step_a.is_holding() is True
    con.execute("UPDATE step SET state = ? WHERE node = ?", (StepState.FAILED.value, 2))
    assert step_a.is_holding() is False


def test_set_state_deferred_rejects_non_pending_state(con):
    """`deferred=True` combined with a state other than PENDING is rejected by the
    step table's deferred/state CHECK constraint (see STEP_SCHEMA).

    `Step.set_state()` used to raise `ValueError` for this in Python; it no longer
    duplicates the check, so this is now only caught at the database level.
    """
    _insert_step(con, 2, 1)
    step_a = _make_step(con, 2)
    with pytest.raises(sqlite3.IntegrityError):
        step_a.set_state(StepState.FAILED, deferred=True)


def test_state_update_to_checking_pending_does_not_disturb_zero_holding(con):
    """The trigger is a no-op (does not error or misfire) when `_holding` is already 0."""
    _insert_step(con, 2, 1)
    step_a = _make_step(con, 2)
    con.execute("UPDATE step SET state = ? WHERE node = ?", (StepState.CHECKING.value, 2))
    con.execute("UPDATE step SET state = ? WHERE node = ?", (StepState.PENDING.value, 2))
    assert step_a.is_holding() is False


def test_state_update_to_running_does_not_reset_holding(con):
    """A step re-entering RUNNING is the one state where a nonzero `_holding` is legitimate."""
    _insert_step(con, 2, 1)
    step_a = _make_step(con, 2)
    con.execute("UPDATE step SET state = ? WHERE node = ?", (StepState.RUNNING.value, 2))
    step_a.hold()
    con.execute("UPDATE step SET state = ? WHERE node = ?", (StepState.RUNNING.value, 2))
    assert step_a.is_holding() is True


def test_release_without_hold_raises(con):
    """Calling `release()` without a preceding `hold()` raises rather than corrupting state."""
    _insert_step(con, 2, 1)
    step_a = _make_step(con, 2)
    with pytest.raises(GraphError):
        step_a.release()


def test_release_more_than_hold_raises(con):
    """A `release()` in excess of the matching `hold()` calls raises instead of taking the
    counter negative.
    """
    _insert_step(con, 2, 1)
    step_a = _make_step(con, 2)
    step_a.hold()
    step_a.release()
    with pytest.raises(GraphError):
        step_a.release()


def test_state_update_flags_check_safe(con):
    """The step_flag_check_safe trigger sets _check_safe on any state write."""
    _insert_step(con, 2, 1)
    con.execute("UPDATE step SET state = ? WHERE node = ?", (StepState.RUNNING.value, 2))
    check_safe = con.execute("SELECT _check_safe FROM step WHERE node = 2").fetchone()[0]
    assert check_safe == 1


def test_same_value_state_update_flags_check_safe(con):
    """The trigger fires on assignment, not on an actual value change."""
    _insert_step(con, 2, 1)
    state = con.execute("SELECT state FROM step WHERE node = 2").fetchone()[0]
    con.execute("UPDATE step SET state = ? WHERE node = ?", (state, 2))
    check_safe = con.execute("SELECT _check_safe FROM step WHERE node = 2").fetchone()[0]
    assert check_safe == 1


def test_duration_update_flags_check_after(con):
    """The step_flag_check_after_duration trigger sets _check_after on any duration write."""
    _insert_step(con, 2, 1)
    con.execute("UPDATE step SET duration = ? WHERE node = ?", (2.0, 2))
    check_after = con.execute("SELECT _check_after FROM step WHERE node = 2").fetchone()[0]
    assert check_after == 1


def test_truncate_output_unlimited():
    """A non-positive max_bytes returns the content unchanged, even when large."""
    content = "x" * 10_000
    assert truncate_output(content, 0) is content
    assert truncate_output(content, -1) is content


def test_truncate_output_under_limit():
    """Content within the byte budget is returned unchanged."""
    content = "hello\n"
    assert truncate_output(content, 100) == content


def test_truncate_output_over_limit():
    """Content over the budget is cut and a sentinel line is appended."""
    content = "abcdefghij"  # 10 ASCII bytes
    result = truncate_output(content, 5)
    assert result == "abcde\n[output truncated at 5 bytes]\n"
    # The kept portion stays within the byte budget.
    assert len(result.split("\n")[0].encode("utf-8")) <= 5


def test_truncate_output_multibyte_boundary():
    """Cutting in the middle of a multi-byte character yields valid UTF-8 within budget."""
    content = "é" * 10  # each 'é' is 2 UTF-8 bytes => 20 bytes total
    # max_bytes 5 lands in the middle of the third 'é' (after 2 full chars = 4 bytes).
    result = truncate_output(content, 5)
    kept = result.split("\n")[0]
    assert kept == "éé"
    # The result is valid text (no replacement characters) and within the budget.
    assert "�" not in result
    assert len(kept.encode("utf-8")) <= 5
    assert result.endswith("[output truncated at 5 bytes]\n")
