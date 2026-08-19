# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Wrapper for SQLite3 functionality."""

import asyncio
import contextlib
import csv
import inspect
import json
import logging
import os
import sqlite3
import time
from collections.abc import Generator, Iterable, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Self

import attrs

from .cattrs import json_converter
from .path import StrPath, coerce_path

__all__ = (
    "DBSession",
    "connect",
    "escape_like_pattern",
)


logger = logging.getLogger(__name__)


def escape_like_pattern(pattern: str) -> str:
    """Escape a string for use in a LIKE pattern."""
    return pattern.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _format_query_plan(rows: Iterable[tuple[int, int, int, str]]) -> str:
    """Format the rows returned by `EXPLAIN QUERY PLAN` as an indented tree.

    Parameters
    ----------
    rows
        Rows returned by `EXPLAIN QUERY PLAN`,
        each a `(id, parent, notused, detail)` tuple.

    Returns
    -------
    plan
        A multiline string with one `detail` per line,
        indented by four spaces per level of nesting,
        following the `parent` links in `rows`.
    """
    children: dict[int, list[tuple[int, str]]] = {}
    for id_, parent, _notused, detail in rows:
        children.setdefault(parent, []).append((id_, detail))

    lines: list[str] = []

    def recurse(parent_id: int, depth: int) -> None:
        for id_, detail in children.get(parent_id, []):
            lines.append("    " * depth + detail)
            recurse(id_, depth + 1)

    recurse(0, 0)
    return "\n".join(lines)


def connect(path: StrPath, read_only: bool = False, **kwargs: Any) -> sqlite3.Connection:
    """Connect to a SQLite database, with the appropriate settings for StepUp.

    Parameters
    ----------
    path
        The path to the SQLite database file.
    read_only
        If True, open the database in read-only mode.
        Otherwise, open it in read-write mode (creating it if it doesn't exist).
    kwargs
        Additional keyword arguments to pass to `sqlite3.connect()`.

    Returns
    -------
    con
        The SQLite connection object.

    Notes
    -----
    The following deviations from the default settings are used.
    Only foreign key enforcement is applied to read-only connections.
    The remaining pragmas are set on read-write connections.

    - The `cached_statements` parameter is set to a large value
      to improve performance when executing many similar statements.
    - Foreign key enforcement is enabled,
      which is required for the `ON DELETE CASCADE` cleanup of satellite rows.
      This is a per-connection setting (not stored in the database file),
      so it must be set on every connection.
    - The journal mode is set to WAL (Write-Ahead Logging) to allow concurrent reads and writes.
    - The synchronous mode is set to OFF to improve performance,
      at the cost of potential data loss in the event of a hard crash.
      This is ok because a few lost transactions are not critical for StepUp,
      as long as the database is not fully corrupted.
    - The auto_vacuum mode is set to INCREMENTAL to allow incremental vacuuming of the database.
    - Recursive triggers are explicitly kept OFF (SQLite's default).
      Several triggers in `step.py`'s `STEP_SCHEMA` (e.g. `step_flag_check_safe`)
      `UPDATE` the same table they fire on;
      turning this `ON` would go against that design
      and cause them to re-fire on their own writes.
    """
    kwargs = kwargs.copy()
    kwargs.setdefault("cached_statements", 1024)
    path = coerce_path(path)
    if read_only:
        # Use URI mode to open the database in read-only mode.
        # This is necessary because SQLite does not have a separate read-only flag.
        path = f"file:{path}?mode=ro"
        kwargs["uri"] = True
        con = sqlite3.connect(path, **kwargs)
        con.isolation_level = None
        con.execute("PRAGMA foreign_keys = ON")
    else:
        con = sqlite3.connect(path, **kwargs)
        con.isolation_level = None
        con.execute("PRAGMA foreign_keys = ON")
        con.execute("PRAGMA journal_mode = WAL")
        con.execute("PRAGMA synchronous = OFF")
        con.execute("PRAGMA auto_vacuum = INCREMENTAL")
        con.execute("PRAGMA recursive_triggers = OFF")
    return con


@attrs.define(frozen=True)
class QueryKey:
    """Identifies a distinct SQL query by its text and call site.

    Combining the query text with its call site allows the same query text,
    executed from two unrelated places in the code, to be tracked separately.
    """

    query: str = attrs.field()
    """The SQL query text."""

    module_name: str = attrs.field()
    """The `__name__` of the module that called `db.execute()` / `db.executemany()`."""

    line: int = attrs.field()
    """The line number of the call to `db.execute()` / `db.executemany()`."""


@attrs.define
class QueryLog:
    """Properties associated with a single SQL query for logging purposes.

    The query and its call site are not stored here,
    as they make up the `QueryKey` used in a dictionary mapping queries to their logs.
    """

    plan: str = attrs.field()
    """The formatted query plan as returned by `EXPLAIN QUERY PLAN`."""

    query_i: int = attrs.field()
    """A unique integer id assigned to this `QueryKey`, referenced by rows in `SQLLOG_CSV`."""


_SQLLOG_CSV_COLUMNS = (
    "transaction_i",
    "execute_i",
    "query_i",
    "start_ns",
    "duration_ns",
    "nrecords",
)
"""Column names of the `--sqllog` CSV file, in on-disk order."""


def _init_sqllog_csv(path: StrPath) -> None:
    """(Re)create the SQL query log CSV file at `path` and write its header."""
    with open(path, "w", newline="") as fh:
        csv.writer(fh).writerow(_SQLLOG_CSV_COLUMNS)


def _append_sqllog_row(
    path: StrPath,
    transaction_i: int,
    execute_i: int,
    query_i: int,
    start_ns: int,
    duration_ns: int,
    nrecords: int,
) -> None:
    """Append one query-execution row to the SQL query log CSV file at `path`.

    The file is opened and closed for every call, so the write reaches disk synchronously
    and rows stay correctly ordered, mirroring `write_joblog_record()` in `utils.py`.
    """
    with open(path, "a", newline="") as fh:
        csv.writer(fh).writerow(
            (transaction_i, execute_i, query_i, start_ns, duration_ns, nrecords)
        )


@attrs.define
class DBSession:
    """Manages SQLite lifetime (via sync context) and exclusive access (via async context).

    Note that logging and counting is only active when `record` is True.
    Even then, `executemany()` call with an empty parameter sequences are not logged or counted.
    """

    path_db: str | os.PathLike[str] = attrs.field()
    """Path to the SQLite database file.

    The connection is opened and kept private when creating a `DBSession` instance.
    """

    connect_kwargs: dict[str, Any] = attrs.field(factory=dict)
    """Connection parameters to pass to `sqlite3`."""

    record: bool = attrs.field(default=False)
    """If True, record SQL debug information for later inspection with `write_log()`."""

    path_sqlcsv: StrPath | None = attrs.field(default=None)
    """Each `execute()` / `executemany()` call appends a timing row to this CSV file."""

    _con: sqlite3.Connection | None = attrs.field(init=False, default=None)
    """The SQLite connection, or None if closed."""

    _lock: asyncio.Lock = attrs.field(factory=asyncio.Lock, init=False)
    """Asyncio lock to ensure exclusive access to the database connection."""

    _cv: ContextVar[sqlite3.Connection | None] = attrs.field(
        factory=lambda: ContextVar("con_cv", default=None), init=False
    )
    """Context variable holding the connection for the current asyncio task.

    It is None when the lock is not acquired.
    """

    _log: dict[QueryKey, QueryLog] = attrs.field(factory=dict, init=False)
    """Mapping of distinct SQL queries to their associated log information."""

    _transaction_i: int = attrs.field(init=False, default=0)
    """Incremented once per transaction (each `BEGIN IMMEDIATE` in `__aenter__`)."""

    _execute_i: int = attrs.field(init=False, default=0)
    """Counter for the number of `execute()` / `executemany()` calls."""

    #
    # Application lifecycle (Synchronous Context Manager)
    #

    def __attrs_post_init__(self) -> None:
        """Open the database connection and create the SQL query log CSV file when configured."""
        self._con = connect(self.path_db, **self.connect_kwargs)
        if self.path_sqlcsv is not None:
            _init_sqllog_csv(self.path_sqlcsv)

    def _close(self) -> None:
        """Close the database connection."""
        if self._con:
            # This close should flush any pending transactions to disk.
            self._con.close()
            self._con = None

    @classmethod
    @contextmanager
    def open(
        cls,
        path_db: str | os.PathLike[str],
        *,
        path_sqllog: StrPath | None = None,
        path_sqlcsv: StrPath | None = None,
        **connect_kwargs: Any,
    ) -> Generator[Self, None, None]:
        """Open a database connection and yield a `DBSession` instance for exclusive access.

        Parameters
        ----------
        path_sqllog
            When given, `record` is set to `True`
            and `write_log()` is called with this path when the session is closed.
        path_sqlcsv
            When given, `record` is set to `True`
            and a timing row is appended to this CSV file
            on every `execute()` / `executemany()` call.
        connect_kwargs
            Additional keyword arguments to pass to `sqlite3.connect()`.
        """
        record = path_sqllog is not None or path_sqlcsv is not None
        db = cls(path_db, connect_kwargs, record=record, path_sqlcsv=path_sqlcsv)
        with contextlib.ExitStack() as stack:
            stack.callback(db._close)
            if path_sqllog is not None:
                stack.callback(db.write_log, path_sqllog)
            yield db

    def write_log(self, path: StrPath) -> None:
        """Write the recorded SQL debug log to a JSON file.

        The file contains a list of records, one per distinct `QueryKey`,
        each merging the key fields (`query`, `module_name`, `line`)
        with the log fields (`plan`, `query_i`).
        A list is used instead of a mapping keyed by query text,
        because a `QueryKey` cannot be represented as a single JSON object key.
        `query_i` is the id referenced by the `query_i` column of `SQLLOG_CSV`.

        Parameters
        ----------
        path
            The destination for the JSON log file.
        """
        records = [
            json_converter.unstructure(key) | json_converter.unstructure(log)
            for key, log in self._log.items()
        ]
        with open(path, "w") as f:
            json.dump(records, f)

    #
    # Transaction locking (Asynchronous Context Manager)
    #

    async def __aenter__(self) -> None:
        if self._con is None:
            raise RuntimeError("Database connection has already been closed.")
        if self._cv.get() is not None:
            raise RuntimeError("Nested DBSession request detected within the same task.")
        await self._lock.acquire()
        try:
            self._con.execute("BEGIN IMMEDIATE")
            self._transaction_i += 1
            self._cv.set(self._con)
        except Exception:
            self._lock.release()
            raise

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self._cv.set(None)
        try:
            if exc is None:
                if not self._con.in_transaction:
                    raise RuntimeError(
                        "Transaction was closed mid-context (executescript or manual COMMIT?)."
                    )
                self._con.commit()
            else:
                self._con.rollback()
        finally:
            self._lock.release()

    #
    # SQL Execution wrappers
    #

    def _take_con(self) -> sqlite3.Connection:
        """Return the connection.

        It is only accessible while the calling asyncio task holds the lock.
        """
        con = self._cv.get()
        if con is None:
            raise RuntimeError(
                "No active database connection. You must acquire the DBSession first."
            )
        return con

    def execute(self, sql: str, args: Iterable[Any] = ()) -> sqlite3.Cursor:
        """Execute an SQL statement with the given arguments."""
        con = self._take_con()
        if not isinstance(args, (Sequence, Mapping)):
            args = tuple(args)
        if self.record:
            frame = inspect.currentframe().f_back
            module_name = frame.f_globals.get("__name__", "?")
            with self._record_execute(sql, module_name, frame.f_lineno, -1, args):
                return con.execute(sql, args)
        else:
            return con.execute(sql, args)

    def executemany(self, sql: str, seq_of_args: Iterable[Iterable[Any]]) -> sqlite3.Cursor:
        """Execute an SQL statement against all parameter sequences or mappings."""
        con = self._take_con()
        seq_of_args = [
            args if isinstance(args, (Sequence, Mapping)) else tuple(args) for args in seq_of_args
        ]
        if len(seq_of_args) > 0 and self.record:
            frame = inspect.currentframe().f_back
            module_name = frame.f_globals.get("__name__", "?")
            with self._record_execute(
                sql, module_name, frame.f_lineno, len(seq_of_args), seq_of_args[0]
            ):
                return con.executemany(sql, seq_of_args)
        else:
            return con.executemany(sql, seq_of_args)

    @contextmanager
    def _record_execute(
        self, sql: str, module_name: str, line: int, nrecords: int, args: Iterable[Any] = ()
    ) -> Generator[None, None, None]:
        """Time one `execute()` / `executemany()` call and log it.

        Parameters
        ----------
        nrecords
            The number of parameter sequences passed to `executemany()`,
            or -1 for a plain `execute()` call.
        args
            The (first) set of query arguments, used for `EXPLAIN QUERY PLAN`
            the first time this `QueryKey` is seen.
        """
        key = QueryKey(query=sql, module_name=module_name, line=line)
        log = self._log.get(key)
        if log is None:
            con = self._take_con()
            plan_rows = list(con.execute(f"EXPLAIN QUERY PLAN {sql}", args))
            plan = _format_query_plan(plan_rows)
            log = QueryLog(plan=plan, query_i=len(self._log))
            self._log[key] = log

        self._execute_i += 1
        execute_i = self._execute_i
        start_ns = time.monotonic_ns()
        try:
            yield
        finally:
            duration_ns = time.monotonic_ns() - start_ns
            if self.path_sqlcsv is not None:
                _append_sqllog_row(
                    self.path_sqlcsv,
                    self._transaction_i,
                    execute_i,
                    log.query_i,
                    start_ns,
                    duration_ns,
                    nrecords,
                )

    async def initialize(
        self, application_id: int, schema_version: int, schema_blobs: list[str | None]
    ) -> bool:
        """Initialize the database with the given SQL schema.

        Parameters
        ----------
        application_id
            The application ID to set for the database.
        schema_version
            The schema version to set for the database.
        schema_blobs
            A list of SQL schema blobs to execute in order to set up the database.
            `None` entries are skipped.

        Returns
        -------
        empty
            True if the database was empty (new or wiped because of schema mismatch).
            False if it already contained the expected schema.
        """
        empty = False
        await self._lock.acquire()
        try:
            empty = self._con.execute("SELECT count(*) FROM sqlite_master").fetchone()[0] == 0
            if not empty:
                rows = self._con.execute("PRAGMA application_id").fetchone()
                if len(rows) != 1 or rows[0] != application_id:
                    raise ValueError("Invalid database application ID")
                rows = self._con.execute("PRAGMA user_version").fetchone()
                if len(rows) != 1 or rows[0] != schema_version:
                    _wipe_database(self._con)
                    empty = True
            for blob in schema_blobs:
                if blob is None:
                    continue
                self._con.executescript(
                    blob.format(
                        application_id=application_id,
                        schema_version=schema_version,
                    )
                )
            if empty:
                # `VACUUM` cannot run inside a transaction.
                # This method holds the lock but without transaction, so it is fine.
                self._con.execute("VACUUM")
        finally:
            self._lock.release()
        return empty

    #
    # Database maintenance (incremental vacuuming)
    #

    def clean_free_space(self, chunk_size: int = 500, max_pages_to_free: int = 5000) -> int:
        """Check the freelist and incrementally reclaim dead space on disk.

        Returns
        -------
        pages_freed
            An upper bound on the number of pages freed, rounded up to a multiple of `chunk_size`.
        """
        con = self._take_con()

        # 1. Query how many empty pages SQLite is holding onto
        freelist_count = con.execute("PRAGMA freelist_count").fetchone()[0]

        # Only clean up when the freelist holds at least one full chunk
        if freelist_count < chunk_size:
            return 0

        pages_freed = 0
        pages_target = min(freelist_count, max_pages_to_free)

        # 2. Vacuum in small chunks so we don't lock up or spike disk I/O
        while pages_freed < pages_target:
            # We must exhaustively step through the `incremental_vacuum` pragma result
            con.execute("PRAGMA incremental_vacuum(?)", (chunk_size,)).fetchall()
            pages_freed += chunk_size

        return pages_freed

    async def database_maintenance_loop(
        self, stop_event: asyncio.Event, start_delay: float = 3.0, interval: float = 300.0
    ) -> None:
        """Periodically reclaim free disk space in the background.

        The loop exits cleanly when `stop_event` is set.
        """
        wait_time = start_delay
        while not stop_event.is_set():
            try:
                # Wait for the next maintenance run, waking up early when the stop event is set
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(stop_event.wait(), timeout=wait_time)

                # If the loop woke up because the app is stopping, break out early
                if stop_event.is_set():
                    break

                # Execute the maintenance routine under an exclusive transaction lock
                async with self:
                    self.clean_free_space()

                # After the first run, switch to the regular interval
                wait_time = interval

            except asyncio.CancelledError:
                # Let cooperative task cancellation propagate,
                # so this task completes as cancelled instead of being logged as an error
                # by the `BaseException` safeguard below.
                raise
            except BaseException:
                # Safeguard: log unexpected errors here,
                # so a database hiccup does not propagate out of this background task.
                logger.error("Error during database maintenance loop", exc_info=True)
                # Exit the loop on error to avoid repeated failures
                return


def _wipe_database(con: sqlite3.Connection):
    """Remove all tables and indexes from an SQLite database.

    This function is not to be used inside a transaction,
    because it temporarily disables foreign key constraints.
    """
    try:
        con.execute("PRAGMA foreign_keys = OFF")
        # Drop all tables
        rows = list(
            con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        )
        for (table,) in rows:
            con.execute(f"DROP TABLE IF EXISTS '{table}'")
        # Drop all indexes
        rows = list(
            con.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'"
            )
        )
        for (index,) in rows:
            con.execute(f"DROP INDEX IF EXISTS '{index}'")
    finally:
        con.execute("PRAGMA foreign_keys = ON")
