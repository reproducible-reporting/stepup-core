# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Tests for stepup.core.sqlite3."""

import csv
import inspect
import json

import pytest

from stepup.core.sqlite3 import DBSession, _format_query_plan, connect

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


def test_connect_sets_recursive_triggers_off():
    """step.py's triggers UPDATE the table they fire on, which requires this to stay OFF."""
    con = connect(":memory:")
    assert con.execute("PRAGMA recursive_triggers").fetchone()[0] == 0


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
            insert_line = inspect.currentframe().f_lineno + 2
            for value in (1, 2):
                db.execute("INSERT INTO t VALUES (?)", (value,))
        # The log file is only written once the session is closed.
        assert not path_sqllog.is_file()

    assert path_sqllog.is_file()
    with open(path_sqllog) as fh:
        log = json.load(fh)

    assert len(log) == 2
    by_query = {record["query"]: record for record in log}
    assert set(by_query) == {"CREATE TABLE t (a INTEGER)", "INSERT INTO t VALUES (?)"}

    create_entry = by_query["CREATE TABLE t (a INTEGER)"]
    assert isinstance(create_entry["query_i"], int)
    assert isinstance(create_entry["plan"], str)
    assert create_entry["module_name"] == __name__

    insert_entry = by_query["INSERT INTO t VALUES (?)"]
    assert insert_entry["module_name"] == __name__
    assert insert_entry["line"] == insert_line
    assert insert_entry["query_i"] != create_entry["query_i"]


async def test_sqllog_csv_rows(tmp_path):
    path_sqlcsv = tmp_path / "sqllog.csv"
    with DBSession.open(":memory:", path_sqlcsv=path_sqlcsv) as db:
        assert db.record
        # The CSV file (header only) is created as soon as the session opens.
        with open(path_sqlcsv, newline="") as fh:
            header = next(csv.reader(fh))
        assert header == [
            "transaction_i",
            "execute_i",
            "query_i",
            "start_ns",
            "duration_ns",
            "nrecords",
        ]
        async with db:
            db.execute("CREATE TABLE t (a INTEGER)")
            db.executemany("INSERT INTO t VALUES (?)", [(1,), (2,)])

    with open(path_sqlcsv, newline="") as fh:
        _header, create_row, insert_row = csv.reader(fh)

    # Both statements ran in the same (only) transaction.
    assert create_row[0] == insert_row[0] == "1"
    # Each distinct call site gets its own query_i.
    assert create_row[1] != insert_row[1]
    # execute_i increments across both calls.
    assert create_row[1] == "1"
    assert insert_row[1] == "2"
    # nrecords: -1 for execute(), len(seq_of_args) for executemany().
    assert create_row[5] == "-1"
    assert insert_row[5] == "2"
