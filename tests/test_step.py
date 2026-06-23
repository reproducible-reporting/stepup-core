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
"""Unit tests for stepup.core.step."""

import pytest

from stepup.core.cascade import CASCADE_SCHEMA
from stepup.core.enums import Need, StepState
from stepup.core.sqlite3 import connect
from stepup.core.step import RECURSIVE_CHECK_WITH_PRODUCTS, STEP_SCHEMA, truncate_output


@pytest.fixture
def con():
    """In-memory SQLite connection with cascade + step schemas and a root node."""
    c = connect(":memory:")
    c.executescript(CASCADE_SCHEMA.format(application_id=0, schema_version=0))
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
        " (node, state, need, duration, rescheduled_info, subshell,"
        " _safe, _check_safe, _implied_need, _tail_time, _check_after)"
        " VALUES (?, ?, ?, 1.0, '', 0, 0, 0, ?, 1.0, 0)",
        (node_id, StepState.PENDING.value, Need.DEFAULT.value, Need.DEFAULT.value),
    )


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


def test_check_with_products_flags_step_and_all_products(con):
    """The flagged step and its (recursive) product steps must be flagged."""
    # Creator chain: root(1) -> A(2) -> B(3) -> C(4)
    _insert_step(con, 2, 1)
    _insert_step(con, 3, 2)
    _insert_step(con, 4, 3)
    con.execute(RECURSIVE_CHECK_WITH_PRODUCTS, (2,))
    assert _flagged(con) == {2, 3, 4}


def test_check_with_products_does_not_flag_sibling_subtree(con):
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


def test_check_with_products_leaf_flags_only_itself(con):
    """A leaf step (one that creates nothing) must flag only itself."""
    # root(1) -> A(2) -> B(3); flag the leaf B, which creates no products.
    _insert_step(con, 2, 1)
    _insert_step(con, 3, 2)
    con.execute(RECURSIVE_CHECK_WITH_PRODUCTS, (3,))
    # The flag must not propagate "upward" to the creator A.
    assert _flagged(con) == {3}


def test_check_with_products_single_step(con):
    """Flagging the only step flags exactly that step."""
    _insert_step(con, 2, 1)
    con.execute(RECURSIVE_CHECK_WITH_PRODUCTS, (2,))
    assert _flagged(con) == {2}


def test_truncate_output_unlimited():
    """A non-positive max_size returns the content unchanged, even when large."""
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
    # max_size 5 lands in the middle of the third 'é' (after 2 full chars = 4 bytes).
    result = truncate_output(content, 5)
    kept = result.split("\n")[0]
    assert kept == "éé"
    # The result is valid text (no replacement characters) and within the budget.
    assert "�" not in result
    assert len(kept.encode("utf-8")) <= 5
    assert result.endswith("[output truncated at 5 bytes]\n")
