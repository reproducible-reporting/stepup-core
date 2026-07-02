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
"""Tests for stepup.core.sqlite3."""

import json

import pytest

from stepup.core.sqlite3 import DBSession, _format_query_plan

# Rows as returned by `EXPLAIN QUERY PLAN`: (id, parent, notused, detail).
# This mimics a query with a subquery scan feeding a search,
# combined with a top-level sort.
QUERY_PLAN_ROWS = [
    (1, 0, 0, "SCAN nodes"),
    (2, 1, 0, "SEARCH files USING INDEX files_path (path=?)"),
    (3, 0, 0, "USE TEMP B-TREE FOR ORDER BY"),
]

EXPECTED_QUERY_PLAN = """\
SCAN nodes
    SEARCH files USING INDEX files_path (path=?)
USE TEMP B-TREE FOR ORDER BY"""


def test_format_query_plan():
    assert _format_query_plan(QUERY_PLAN_ROWS) == EXPECTED_QUERY_PLAN


def test_format_query_plan_empty():
    assert _format_query_plan([]) == ""


async def test_detect_nested_dblock_in_same_task():
    with DBSession.open(":memory:") as db:
        async with db:
            with pytest.raises(RuntimeError):
                async with db:
                    pass


async def test_no_sqllog_by_default(tmp_path):
    with DBSession.open(":memory:") as db:
        assert not db.record
        async with db:
            db.execute("CREATE TABLE t (a INTEGER)")
    assert list(tmp_path.iterdir()) == []


async def test_sqllog_written_on_close(tmp_path):
    path_sqllog = tmp_path / "sqllog.json"
    with DBSession.open(":memory:", path_sqllog=path_sqllog) as db:
        assert db.record
        async with db:
            db.execute("CREATE TABLE t (a INTEGER)")
            db.execute("INSERT INTO t VALUES (?)", (1,))
            db.execute("INSERT INTO t VALUES (?)", (2,))
        # The log file is only written once the session is closed.
        assert not path_sqllog.is_file()

    assert path_sqllog.is_file()
    with open(path_sqllog) as fh:
        log = json.load(fh)

    assert set(log) == {"CREATE TABLE t (a INTEGER)", "INSERT INTO t VALUES (?)"}
    create_entry = log["CREATE TABLE t (a INTEGER)"]
    assert create_entry["count"] == 1
    assert create_entry["wtime"] >= 0.0
    assert isinstance(create_entry["plan"], str)
    insert_entry = log["INSERT INTO t VALUES (?)"]
    assert insert_entry["count"] == 2
    assert insert_entry["wtime"] >= 0.0
