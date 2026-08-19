# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Tests for stepup.core.sqlite3."""

import asyncio
import csv
import inspect
import json
import logging
import sqlite3

import pytest

from stepup.core.sqlite3 import DBSession, SQLLog, _format_query_plan, connect, prefix_clause


def _select_like_prefix(labels: list[str], prefix: str) -> list[str]:
    """Store `labels` in an in-memory table and select those starting with `prefix`."""
    con = connect(":memory:")
    con.execute("CREATE TABLE node (label TEXT)")
    con.executemany("INSERT INTO node VALUES (?)", [(label,) for label in labels])
    clause, pattern = prefix_clause("label", prefix)
    sql = f"SELECT label FROM node WHERE {clause} ORDER BY label"
    return [row[0] for row in con.execute(sql, (pattern,))]


def test_like_prefix_matches_special_characters():
    """A prefix containing `_`, `%` and the escape character matches literally."""
    labels = ["p_q%r\\s/file.txt", "p_q%r\\s/sub/file.txt", "other.txt"]
    assert _select_like_prefix(labels, "p_q%r\\s/") == [
        "p_q%r\\s/file.txt",
        "p_q%r\\s/sub/file.txt",
    ]


def test_like_prefix_underscore_is_not_a_wildcard():
    """Without the `ESCAPE` clause, `aXb/x.txt` would also match the prefix `a_b/`."""
    labels = ["a_b/x.txt", "aXb/x.txt"]
    assert _select_like_prefix(labels, "a_b/") == ["a_b/x.txt"]


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


def test_connect_read_write_pragmas(tmp_path):
    """A read-write connection gets WAL journalling and incremental auto vacuum."""
    con = connect(tmp_path / "graph.db")
    assert con.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    # 2 is INCREMENTAL. Without it, `reclaim_free_space` has nothing to reclaim.
    assert con.execute("PRAGMA auto_vacuum").fetchone()[0] == 2
    assert con.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    con.close()


def test_connect_read_only(tmp_path):
    """A read-only connection reads, rejects writes, and still enforces foreign keys."""
    path_db = tmp_path / "graph.db"
    con = connect(path_db)
    con.execute("CREATE TABLE t (a INTEGER)")
    con.execute("INSERT INTO t VALUES (1)")
    con.close()

    con = connect(path_db, read_only=True)
    try:
        assert con.execute("SELECT a FROM t").fetchall() == [(1,)]
        with pytest.raises(sqlite3.OperationalError):
            con.execute("INSERT INTO t VALUES (2)")
        assert con.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        con.close()


def test_connect_read_only_uri_special_characters(tmp_path):
    """A path with URI syntax in it is opened read-only, instead of some other database.

    Without escaping, the `?` and `#` start the query and fragment part of the URI,
    so `mode=ro` is never parsed and a different, empty database is created read-write.
    """
    for name in "plain.db", "we#ird.db", "qu?ery.db":
        path_db = tmp_path / name
        con = connect(path_db)
        con.execute("CREATE TABLE t (a INTEGER)")
        con.execute("INSERT INTO t VALUES (1)")
        con.commit()
        con.close()
        con = connect(path_db, read_only=True)
        try:
            assert con.execute("SELECT a FROM t").fetchall() == [(1,)]
            with pytest.raises(sqlite3.OperationalError, match="readonly"):
                con.execute("INSERT INTO t VALUES (2)")
        finally:
            con.close()
    # No database was created under a truncated name.
    assert sorted(path.name for path in tmp_path.glob("*.db")) == [
        "plain.db",
        "qu?ery.db",
        "we#ird.db",
    ]


async def test_spawned_task_waits_for_lock():
    """A task spawned inside a transaction holds nothing and waits its turn.

    It inherits a copy of the parent's context,
    so a guard based on a `ContextVar` would wrongly reject it as a nested request.
    """

    async def child(db: DBSession) -> None:
        async with db:
            db.execute("SELECT 1")

    with DBSession.open(":memory:") as db:
        async with db:
            task = asyncio.create_task(child(db))
            await asyncio.sleep(0)
            assert not task.done()
        await asyncio.wait_for(task, timeout=5.0)


async def test_spawned_task_after_parent_released():
    """A task spawned inside a transaction can still take the lock once the parent is out."""

    async def child(db: DBSession, go: asyncio.Event) -> None:
        await go.wait()
        async with db:
            db.execute("SELECT 1")

    with DBSession.open(":memory:") as db:
        go = asyncio.Event()
        async with db:
            task = asyncio.create_task(child(db, go))
            await asyncio.sleep(0)
        go.set()
        await asyncio.wait_for(task, timeout=5.0)


async def test_execute_outside_own_transaction():
    """A task cannot execute statements in a transaction that another task owns."""

    async def outsider(db: DBSession, started: asyncio.Event) -> None:
        await started.wait()
        with pytest.raises(RuntimeError, match="No open transaction"):
            db.execute("SELECT 1")

    with DBSession.open(":memory:") as db:
        started = asyncio.Event()
        task = asyncio.create_task(outsider(db, started))
        async with db:
            started.set()
            await asyncio.wait_for(task, timeout=5.0)


async def test_detect_nested_dblock_in_same_task():
    with DBSession.open(":memory:") as db:
        async with db:
            with pytest.raises(RuntimeError):
                async with db:
                    pass


async def test_detect_nested_autocommit_in_transaction():
    """`_autocommit_con()` inside a transaction is rejected
    instead of waiting on the lock forever.
    """

    async def nested(db: DBSession) -> None:
        async with db:
            with pytest.raises(RuntimeError, match="Nested DBSession"):
                async with db._autocommit_con():
                    pass

    with DBSession.open(":memory:") as db:
        await asyncio.wait_for(nested(db), timeout=5.0)


async def test_detect_nested_transaction_in_autocommit():
    """A transaction inside `_autocommit_con()` is rejected
    instead of waiting on the lock forever.
    """

    async def nested(db: DBSession) -> None:
        async with db._autocommit_con():
            with pytest.raises(RuntimeError, match="Nested DBSession"):
                async with db:
                    pass

    with DBSession.open(":memory:") as db:
        await asyncio.wait_for(nested(db), timeout=5.0)


async def test_detect_nested_autocommit_in_autocommit():
    """`_autocommit_con()` inside `_autocommit_con()` is rejected
    instead of waiting on the lock forever.
    """

    async def nested(db: DBSession) -> None:
        async with db._autocommit_con():
            with pytest.raises(RuntimeError, match="Nested DBSession"):
                async with db._autocommit_con():
                    pass

    with DBSession.open(":memory:") as db:
        await asyncio.wait_for(nested(db), timeout=5.0)


async def test_autocommit_then_transaction_in_same_task():
    """Sequential use of both contexts in one task is not nesting, which is how startup works."""

    async def sequential(db: DBSession) -> None:
        async with db._autocommit_con() as con:
            con.executescript("CREATE TABLE t (a INTEGER)")
        async with db:
            db.execute("INSERT INTO t VALUES (1)")
        async with db._autocommit_con() as con:
            assert con.execute("SELECT a FROM t").fetchall() == [(1,)]

    with DBSession.open(":memory:") as db:
        await asyncio.wait_for(sequential(db), timeout=5.0)


async def test_execute_in_autocommit():
    """The autocommit context does not grant access to `execute()`."""

    async def execute_inside(db: DBSession) -> None:
        async with db._autocommit_con():
            with pytest.raises(RuntimeError, match="No open transaction"):
                db.execute("SELECT 1")

    with DBSession.open(":memory:") as db:
        await asyncio.wait_for(execute_inside(db), timeout=5.0)


async def test_transaction_after_close():
    """A closed session refuses a transaction with a clear error and keeps its lock free."""
    with DBSession.open(":memory:") as db:
        pass
    with pytest.raises(RuntimeError, match="already been closed"):
        async with db:
            pass
    assert not db._lock.locked()


async def test_autocommit_after_close():
    """A closed session refuses the autocommit context with a clear error."""
    with DBSession.open(":memory:") as db:
        pass
    with pytest.raises(RuntimeError, match="already been closed"):
        async with db._autocommit_con():
            pass
    assert not db._lock.locked()


async def test_transaction_lock_serializes_tasks():
    """Two tasks cannot hold the session at the same time."""
    with DBSession.open(":memory:") as db:
        marks = []

        async def worker(name: str) -> None:
            async with db:
                marks.append(f"enter {name}")
                # This is the only chance the other task gets to run,
                # and it may not use it to enter the transaction.
                await asyncio.sleep(0)
                marks.append(f"exit {name}")

        await asyncio.gather(worker("a"), worker("b"))

    assert marks == ["enter a", "exit a", "enter b", "exit b"]


async def test_transaction_rollback_on_error():
    """An exception in the body rolls the transaction back and propagates."""

    async def insert_and_raise(db: DBSession) -> None:
        async with db:
            db.execute("INSERT INTO t VALUES (1)")
            raise ValueError("Something went wrong halfway.")

    with DBSession.open(":memory:") as db:
        async with db:
            db.execute("CREATE TABLE t (a INTEGER)")
        with pytest.raises(ValueError):
            await insert_and_raise(db)
        async with db:
            assert db.execute("SELECT count(*) FROM t").fetchall() == [(0,)]


async def test_transaction_closed_mid_context_by_commit():
    """Committing behind the session's back is detected instead of silently accepted."""

    async def commit_inside(db: DBSession) -> None:
        async with db:
            db.execute("COMMIT")

    with DBSession.open(":memory:") as db, pytest.raises(RuntimeError, match="closed mid-context"):
        await commit_inside(db)


async def test_transaction_closed_mid_context_by_executescript():
    """`executescript` commits the pending transaction first, which is also detected."""

    async def executescript_inside(db: DBSession) -> None:
        async with db:
            db._require_transaction_con().executescript("CREATE TABLE t (a INTEGER)")

    with DBSession.open(":memory:") as db, pytest.raises(RuntimeError, match="closed mid-context"):
        await executescript_inside(db)


async def test_execute_without_transaction():
    """`execute()` and `executemany()` are unavailable outside `async with`."""
    with DBSession.open(":memory:") as db:
        with pytest.raises(RuntimeError, match="No open transaction"):
            db.execute("SELECT 1")
        with pytest.raises(RuntimeError, match="No open transaction"):
            db.executemany("SELECT ?", [(1,)])


async def test_execute_str_args():
    """A bare string is rejected instead of being bound character by character."""
    with DBSession.open(":memory:") as db:
        async with db:
            db.execute("CREATE TABLE t (a TEXT)")
            with pytest.raises(TypeError, match="must not be a string"):
                db.execute("INSERT INTO t VALUES (?)", "ab")
            with pytest.raises(TypeError, match="must not be a string"):
                db.executemany("INSERT INTO t VALUES (?)", ["ab"])
            assert db.execute("SELECT count(*) FROM t").fetchone()[0] == 0


async def test_no_sqllog_by_default(tmp_path):
    with DBSession.open(":memory:") as db:
        assert db.sqllog is None
        async with db:
            db.execute("CREATE TABLE t (a INTEGER)")
    assert list(tmp_path.iterdir()) == []


async def test_sqllog_written_on_exit(tmp_path):
    path_queries = tmp_path / "sqllog.json"
    with SQLLog(path_queries=path_queries, path_timings=tmp_path / "sqllog.csv") as sqllog:
        with DBSession.open(":memory:", sqllog=sqllog) as db:
            assert db.sqllog is not None
            async with db:
                db.execute("CREATE TABLE t (a INTEGER)")
                insert_line = inspect.currentframe().f_lineno + 2
                for value in (1, 2):
                    db.execute("INSERT INTO t VALUES (?)", (value,))
        # The log file is only written once the recorder's context is left,
        # which outlives the session.
        assert not path_queries.is_file()

    assert path_queries.is_file()
    with open(path_queries) as fh:
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
    path_timings = tmp_path / "sqllog.csv"
    sqllog = SQLLog(path_queries=tmp_path / "sqllog.json", path_timings=path_timings)
    # Constructing the recorder does not touch the disk.
    assert not path_timings.exists()
    with sqllog, DBSession.open(":memory:", sqllog=sqllog) as db:
        assert db.sqllog is not None
        # The CSV file (header only) is created as soon as the recorder is entered.
        with open(path_timings, newline="") as fh:
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

    with open(path_timings, newline="") as fh:
        _header, create_row, insert_row = csv.reader(fh)

    # Both statements ran in the same (only) transaction.
    assert create_row[0] == insert_row[0] == "1"
    # Each distinct call site gets its own query_i.
    assert create_row[2] != insert_row[2]
    # execute_i increments across both calls.
    assert create_row[1] == "1"
    assert insert_row[1] == "2"
    # nrecords: 1 for execute(), len(seq_of_args) for executemany().
    assert create_row[5] == "1"
    assert insert_row[5] == "2"


APPLICATION_ID = 123456789

SCHEMA_ONE = """
CREATE TABLE IF NOT EXISTS one (name TEXT);
"""

SCHEMA_TWO = """
CREATE TABLE IF NOT EXISTS two (name TEXT);
"""


def _table_names(path_db) -> set[str]:
    """The names of the non-internal tables in `path_db`, read on a separate connection."""
    con = connect(path_db)
    try:
        rows = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
        return {row[0] for row in rows}
    finally:
        con.close()


async def test_apply_schema_fresh_database(tmp_path):
    """A fresh database is reported as fresh and gets the schema and its pragmas."""
    path_db = tmp_path / "fresh.db"
    with DBSession.open(path_db) as db:
        assert await db.apply_schema(APPLICATION_ID, 1, [SCHEMA_ONE])
        async with db:
            assert db.execute("PRAGMA application_id").fetchone()[0] == APPLICATION_ID
            assert db.execute("PRAGMA user_version").fetchone()[0] == 1
            db.execute("INSERT INTO one VALUES ('a')")
    assert _table_names(path_db) == {"one"}


async def test_apply_schema_existing_database(tmp_path):
    """Reopening an unchanged database is not reported as fresh, and the schema is reapplied."""
    path_db = tmp_path / "existing.db"
    with DBSession.open(path_db) as db:
        await db.apply_schema(APPLICATION_ID, 1, [SCHEMA_ONE])
        async with db:
            db.execute("INSERT INTO one VALUES ('a')")
    with DBSession.open(path_db) as db:
        assert not await db.apply_schema(APPLICATION_ID, 1, [SCHEMA_ONE])
        async with db:
            assert db.execute("SELECT name FROM one").fetchall() == [("a",)]


async def test_apply_schema_version_mismatch_wipes(tmp_path):
    """A database with a different schema version is wiped and reported as fresh."""
    path_db = tmp_path / "outdated.db"
    with DBSession.open(path_db) as db:
        await db.apply_schema(APPLICATION_ID, 1, [SCHEMA_ONE])
        async with db:
            db.execute("INSERT INTO one VALUES ('a')")
    with DBSession.open(path_db) as db:
        assert await db.apply_schema(APPLICATION_ID, 2, [SCHEMA_TWO])
        async with db:
            assert db.execute("PRAGMA user_version").fetchone()[0] == 2
    assert _table_names(path_db) == {"two"}


async def test_apply_schema_braces_in_script(tmp_path):
    """A schema script is executed verbatim, so braces in SQL need no escaping."""
    path_db = tmp_path / "braces.db"
    schema = "CREATE TABLE IF NOT EXISTS brace (data TEXT DEFAULT '{\"a\": 1}');"
    with DBSession.open(path_db) as db:
        assert await db.apply_schema(APPLICATION_ID, 1, [schema])
        async with db:
            db.execute("INSERT INTO brace DEFAULT VALUES")
            assert db.execute("SELECT data FROM brace").fetchone()[0] == '{"a": 1}'


async def test_apply_schema_application_id_mismatch_raises(tmp_path):
    """A database of another application raises and is left untouched."""
    path_db = tmp_path / "foreign.db"
    with DBSession.open(path_db) as db:
        await db.apply_schema(APPLICATION_ID, 1, [SCHEMA_ONE])
        async with db:
            db.execute("INSERT INTO one VALUES ('a')")
    with DBSession.open(path_db) as db, pytest.raises(ValueError):
        await db.apply_schema(APPLICATION_ID + 1, 1, [SCHEMA_TWO])
    assert _table_names(path_db) == {"one"}
    with DBSession.open(path_db) as db:
        async with db:
            assert db.execute("SELECT name FROM one").fetchall() == [("a",)]


async def _build_freelist(db: DBSession, nrow: int = 800) -> int:
    """Insert and delete `nrow` oversized rows, and return the resulting freelist length.

    Each row nearly fills a 4096-byte page,
    so the freelist ends up at least `nrow` pages long.
    """
    async with db:
        db.execute("CREATE TABLE t (blob BLOB)")
        db.executemany("INSERT INTO t VALUES (?)", [(b"x" * 4000,) for _ in range(nrow)])
    async with db:
        db.execute("DELETE FROM t")
    async with db:
        return db.execute("PRAGMA freelist_count").fetchone()[0]


async def test_reclaim_free_space_below_one_chunk(tmp_path):
    """Nothing is reclaimed while the freelist is shorter than one chunk."""
    with DBSession.open(tmp_path / "graph.db") as db:
        async with db:
            db.execute("CREATE TABLE t (a INTEGER)")
        assert await db.reclaim_free_space(pages_per_chunk=100) == 0


async def test_reclaim_free_space_shrinks_freelist(tmp_path):
    """The number of pages reported is the number of pages the freelist lost."""
    with DBSession.open(tmp_path / "graph.db") as db:
        freelist_before = await _build_freelist(db)
        assert freelist_before > 100
        pages_freed = await db.reclaim_free_space(pages_per_chunk=100)
        async with db:
            freelist_after = db.execute("PRAGMA freelist_count").fetchone()[0]
    assert pages_freed > 0
    assert freelist_before - freelist_after == pages_freed


async def test_reclaim_free_space_respects_max_pages_to_free(tmp_path):
    """A single run never exceeds `max_pages_to_free`, leaving the rest for the next one."""
    with DBSession.open(tmp_path / "graph.db") as db:
        freelist_before = await _build_freelist(db)
        assert freelist_before > 250
        pages_freed = await db.reclaim_free_space(pages_per_chunk=100, max_pages_to_free=250)
        async with db:
            freelist_after = db.execute("PRAGMA freelist_count").fetchone()[0]
    # The cap is not a multiple of the chunk size, so the last partial chunk is skipped.
    assert pages_freed == 200
    assert freelist_after == freelist_before - 200


async def test_reclaim_loop_stops_before_first_run(tmp_path):
    """An event that is already set is noticed before the start delay is waited out."""
    with DBSession.open(tmp_path / "graph.db") as db:
        stop_event = asyncio.Event()
        stop_event.set()
        await asyncio.wait_for(db.reclaim_loop(stop_event, start_delay=300.0), timeout=5.0)


async def test_reclaim_loop_stops_during_wait(tmp_path):
    """Setting the event wakes the loop out of its wait."""
    with DBSession.open(tmp_path / "graph.db") as db:
        stop_event = asyncio.Event()
        task = asyncio.create_task(db.reclaim_loop(stop_event, start_delay=300.0))
        # Give the loop a chance to reach its wait before the event is set.
        for _ in range(3):
            await asyncio.sleep(0)
        stop_event.set()
        await asyncio.wait_for(task, timeout=5.0)


async def test_reclaim_loop_returns_on_error(tmp_path, caplog, monkeypatch):
    """An error is logged once and ends the loop, instead of propagating or spinning."""

    async def boom(self, *args, **kwargs):
        raise RuntimeError("The database is having a bad day.")

    monkeypatch.setattr(DBSession, "reclaim_free_space", boom)
    with DBSession.open(tmp_path / "graph.db") as db:
        stop_event = asyncio.Event()
        with caplog.at_level(logging.ERROR):
            await asyncio.wait_for(
                db.reclaim_loop(stop_event, start_delay=0.0, interval=0.0), timeout=5.0
            )
    assert [record.levelname for record in caplog.records] == ["ERROR"]
    assert not stop_event.is_set()
