# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Wrapper for SQLite3 functionality.

The module holds four parts.
`prefix_clause()` builds a LIKE predicate that matches a column against a literal prefix.
`connect()` collects the connection settings that every StepUp database is opened with.
`DBSession` serializes all access to one such connection,
with a lock and explicit transactions.
`SQLLog` records query plans and execution timings for a session.

Some pragmas take an argument that cannot be a placeholder,
so those arguments are interpolated into the SQL text.
All of them are integers in this module, so there is nothing to escape.
"""

import asyncio
import contextlib
import csv
import inspect
import json
import logging
import sqlite3
import time
import urllib.parse
from collections.abc import AsyncGenerator, Generator, Iterable, Mapping, Sequence
from types import FrameType, TracebackType
from typing import Any, Self

import attrs

from .cattrs import json_converter
from .path import StrPath, coerce_path

__all__ = (
    "DBSession",
    "SQLArgs",
    "SQLLog",
    "connect",
    "prefix_clause",
)


logger = logging.getLogger(__name__)

SQLArgs = Sequence[Any] | Mapping[str, Any]
"""Arguments bound to the placeholders of one SQL statement."""


#
# LIKE pattern helpers
#


def prefix_clause(column: str, prefix: str) -> tuple[str, str]:
    """Build a LIKE predicate and its argument for matching a column against a prefix.

    Parameters
    ----------
    column
        The column to match, interpolated into the SQL text.
        This must be a literal from the calling code, never user input.
    prefix
        The literal prefix to match.
        Characters with a special meaning in LIKE patterns are escaped.

    Returns
    -------
    clause
        An SQL predicate containing a single placeholder.
    pattern
        The value to bind to that placeholder.

    Notes
    -----
    SQLite only honors the escape character when the query carries an `ESCAPE` clause,
    so the predicate and its argument are built together and must be used together.
    """
    escaped = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"{column} LIKE ? ESCAPE '\\'", f"{escaped}%"


#
# Connection layer
#


def connect(path: StrPath, *, read_only: bool = False, **kwargs: Any) -> sqlite3.Connection:
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
    The statement cache, autocommit mode and foreign key enforcement
    are applied to every connection.
    The pragmas below them only affect how the database is modified,
    so they are applied to read-write connections only.

    - The `cached_statements` parameter is set to a large value
      to improve performance when executing many similar statements.
    - Autocommit mode is enabled,
      so that transactions start and end only where the code says so,
      with an explicit `BEGIN` and `commit()`.
      Without it, the legacy transaction control of the `sqlite3` module
      opens and commits transactions of its own,
      which fights with the explicit transaction boundaries.
    - Foreign key enforcement is enabled,
      which is required for the `ON DELETE CASCADE` cleanup of satellite rows.
      This is a per-connection setting (not stored in the database file),
      so it must be set on every connection.
    - The auto_vacuum mode is set to INCREMENTAL to allow incremental vacuuming of the database.
    - The journal mode is set to WAL (Write-Ahead Logging) to allow concurrent reads and writes.
    - The synchronous mode is set to OFF to improve performance,
      at the cost of potential data loss in the event of a hard crash.
      This is ok because a few lost transactions are not critical for StepUp,
      as long as the database is not fully corrupted.
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
        # Use URI mode to open the database in read-only mode,
        # because SQLite does not have a separate read-only flag.
        # The path is percent-encoded, because a `?` or `#` in a file name would otherwise
        # start the query or fragment part of the URI.
        # SQLite ignores the unknown parameters that result from this,
        # and silently opens (or creates) a different database read-write.
        path = f"file:{urllib.parse.quote(str(path))}?mode=ro"
        kwargs["uri"] = True
    con = sqlite3.connect(path, **kwargs)
    con.isolation_level = None
    con.execute("PRAGMA foreign_keys = ON")
    if not read_only:
        # The auto_vacuum pragma must come first.
        # As of SQLite 3.51, setting the journal mode of a new database writes its header,
        # after which auto_vacuum can only be changed by a full `VACUUM`,
        # and a plain `PRAGMA auto_vacuum` assignment is silently ignored.
        con.execute("PRAGMA auto_vacuum = INCREMENTAL")
        con.execute("PRAGMA journal_mode = WAL")
        con.execute("PRAGMA synchronous = OFF")
        con.execute("PRAGMA recursive_triggers = OFF")
    return con


#
# Query logging
#


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

    @classmethod
    def from_frame(cls, query: str, frame: FrameType) -> Self:
        """Build the key of a query executed at the call site of a stack frame.

        Parameters
        ----------
        query
            The SQL query text.
        frame
            The stack frame of the call site.

        Returns
        -------
        key
            The key identifying `query` as executed from that call site.
        """
        return cls(
            query=query,
            module_name=frame.f_globals.get("__name__", "?"),
            line=frame.f_lineno,
        )


@attrs.define
class QueryInfo:
    """Properties associated with a single SQL query for logging purposes.

    The query and its call site are not stored here,
    as they make up the `QueryKey` used in a dictionary mapping queries to their info.
    """

    plan: str = attrs.field()
    """The formatted query plan as returned by `EXPLAIN QUERY PLAN`."""

    query_i: int = attrs.field()
    """A unique integer id assigned to this `QueryKey`, referenced by rows in the CSV file.

    This must equal the position of the `QueryKey` in the insertion order of the dictionary
    holding these records,
    because `SQLLog._write_query_index()` writes that dictionary out as a list in that order.
    """


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


_SQLLOG_CSV_COLUMNS = (
    "transaction_i",
    "execute_i",
    "query_i",
    "start_ns",
    "duration_ns",
    "nrecords",
)
"""Column names of the `--sqllog` CSV file, in on-disk order."""


@attrs.define
class SQLLog:
    """Records query plans and per-execution timings for a `DBSession`.

    This is a synchronous context manager owning both files it writes,
    and it must be entered before anything is recorded.
    Entering creates the timings file and writes its header,
    leaving writes the query index.
    Constructing a recorder has no effect on disk.

    The query plans are collected in memory and written out in one go when the context is left,
    while the timing rows are appended to the timings file as the executions happen.
    """

    path_queries: StrPath = attrs.field(kw_only=True)
    """Destination of the query index, written when the context is left."""

    path_timings: StrPath = attrs.field(kw_only=True)
    """Destination of the per-execution timing rows, appended as they happen."""

    _queries: dict[QueryKey, QueryInfo] = attrs.field(factory=dict, init=False)
    """Distinct queries seen so far, in first-seen order."""

    _execute_i: int = attrs.field(init=False, default=0)
    """Number of recorded executions so far, used as the row id in the CSV file."""

    def __enter__(self) -> Self:
        """Create the timings file and write its header."""
        with open(self.path_timings, "w", newline="") as fh:
            csv.DictWriter(fh, _SQLLOG_CSV_COLUMNS).writeheader()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Write the recorded query index to the JSON file.

        The index is written whether or not an exception is propagating,
        because the rows already in the CSV file are useless without it.
        """
        self._write_query_index()

    @contextlib.contextmanager
    def time_execute(
        self,
        con: sqlite3.Connection,
        key: QueryKey,
        *,
        transaction_i: int,
        nrecords: int,
        plan_args: SQLArgs = (),
    ) -> Generator[None, None, None]:
        """Time one `execute()` / `executemany()` call and log it.

        Parameters
        ----------
        con
            The connection on which `EXPLAIN QUERY PLAN` is run,
            the first time this key is seen.
        key
            Identifies the query whose execution is being timed.
        transaction_i
            The transaction in which the execution takes place.
        nrecords
            The number of records bound by the call:
            one for `execute()`, the number of parameter sets for `executemany()`.
        plan_args
            The (first) set of query arguments, used for `EXPLAIN QUERY PLAN`
            the first time this `key` is seen.
        """
        info = self._queries.get(key)
        if info is None:
            plan_rows = list(con.execute(f"EXPLAIN QUERY PLAN {key.query}", plan_args))
            info = QueryInfo(plan=_format_query_plan(plan_rows), query_i=len(self._queries))
            self._queries[key] = info

        self._execute_i += 1
        execute_i = self._execute_i
        start_ns = time.monotonic_ns()
        try:
            yield
        finally:
            self._append_csv_row(
                {
                    "transaction_i": transaction_i,
                    "execute_i": execute_i,
                    "query_i": info.query_i,
                    "start_ns": start_ns,
                    "duration_ns": time.monotonic_ns() - start_ns,
                    "nrecords": nrecords,
                }
            )

    def _append_csv_row(self, row: Mapping[str, int]) -> None:
        """Append one query-execution row to the CSV file.

        The row must have exactly the keys in `_SQLLOG_CSV_COLUMNS`.

        The file is opened and closed for every call, so the write reaches disk synchronously
        and rows stay correctly ordered, mirroring `append_joblog_record()` in `job.py`.
        """
        with open(self.path_timings, "a", newline="") as fh:
            csv.DictWriter(fh, _SQLLOG_CSV_COLUMNS).writerow(row)

    def _write_query_index(self) -> None:
        """Write the recorded query index to the JSON file.

        The file contains a list of records, one per distinct `QueryKey`,
        each merging the key fields (`query`, `module_name`, `line`)
        with the info fields (`plan`, `query_i`).
        A list is used instead of a mapping keyed by query text,
        because a `QueryKey` cannot be represented as a single JSON object key.
        `query_i` is the id referenced by the `query_i` column of the CSV file.
        """
        records = [
            json_converter.unstructure(key) | json_converter.unstructure(info)
            for key, info in self._queries.items()
        ]
        with open(self.path_queries, "w") as f:
            json.dump(records, f)


#
# Session helpers
#


def _coerce_args(args: Iterable[Any]) -> SQLArgs:
    """Put the arguments of one SQL statement in a form that `sqlite3` can bind.

    Returns
    -------
    coerced
        `args` itself when it is already a sequence or a mapping,
        or a tuple with its items otherwise.

    Raises
    ------
    TypeError
        When `args` is a string.
        A string is a sequence, so `sqlite3` would bind it character by character,
        which is never what the caller means.
    """
    if isinstance(args, str):
        raise TypeError("SQL arguments must not be a string.")
    if isinstance(args, (Sequence, Mapping)):
        return args
    return tuple(args)


def _wipe_database(con: sqlite3.Connection) -> None:
    """Remove all tables, indexes and views from an SQLite database.

    This is not to be called inside a transaction,
    because SQLite silently ignores a `foreign_keys` pragma there,
    which would leave the constraints in force while the tables are dropped.

    Triggers are not dropped by name,
    because SQLite drops a trigger along with the table or view it is attached to.
    """
    assert not con.in_transaction
    con.execute("PRAGMA foreign_keys = OFF")
    try:
        # Tables come first, because dropping one also drops its indexes and triggers.
        for kind in "table", "index", "view":
            names = [
                name
                for (name,) in con.execute(
                    "SELECT name FROM sqlite_master WHERE type = ? AND name NOT LIKE 'sqlite_%'",
                    (kind,),
                )
            ]
            for name in names:
                # The kind and the name cannot be placeholders.
                # The names come from `sqlite_master`, not from user input.
                con.execute(f"DROP {kind.upper()} IF EXISTS '{name}'")
    finally:
        con.execute("PRAGMA foreign_keys = ON")


@attrs.frozen
class _Held:
    """The exclusive access that one asyncio task holds on the connection."""

    task: asyncio.Task = attrs.field()
    """The task that acquired the connection."""

    con: sqlite3.Connection = attrs.field()
    """The connection that was acquired."""

    opened_transaction: bool = attrs.field()
    """True when a transaction was opened, False when statements run in autocommit mode."""


@attrs.define
class DBSession:
    """Serialize all access to one SQLite database.

    The synchronous context manager `open()` owns the lifetime of the connection.
    Within it, a task takes exclusive access through one of two asynchronous contexts,
    which have different rules:

    - `async with db:` opens a transaction.
      This is the only context in which `execute()` and `executemany()` may be called.
    - `_autocommit_con()` holds the connection without a transaction,
      for statements that cannot run inside one, such as `VACUUM` and `executescript`.

    Neither context may be nested within a task, because the lock is not reentrant:
    a second acquisition by the task that already holds the connection would deadlock,
    so it raises instead.
    Every other task, including one that the holder spawned,
    simply waits for the lock like any independent caller.

    Note that query profiling is only active when `sqllog` is not None.
    Even then, `executemany()` calls with an empty parameter sequence are not logged or counted.
    """

    sqllog: SQLLog | None = attrs.field(default=None, kw_only=True)
    """Profiling recorder, or None when profiling is off."""

    _con: sqlite3.Connection | None = attrs.field(init=False, default=None)
    """The SQLite connection, or None if closed."""

    _lock: asyncio.Lock = attrs.field(factory=asyncio.Lock, init=False)
    """Asyncio lock to ensure exclusive access to the database connection."""

    _held: _Held | None = attrs.field(init=False, default=None)
    """What the task currently holding the connection holds, or None when nobody holds it."""

    _transaction_i: int = attrs.field(init=False, default=0)
    """Incremented once per transaction (each `BEGIN IMMEDIATE` in `__aenter__`)."""

    #
    # Lifetime (synchronous context manager)
    #

    @classmethod
    @contextlib.contextmanager
    def open(
        cls,
        path: StrPath,
        *,
        sqllog: SQLLog | None = None,
        **connect_kwargs: Any,
    ) -> Generator[Self, None, None]:
        """Open a database connection and yield a `DBSession` instance for exclusive access.

        Parameters
        ----------
        path
            The path to the SQLite database file, which is created when it does not exist.
        sqllog
            An entered `SQLLog`, when every `execute()` / `executemany()` call
            must be timed and logged.
            The recorder writes its own files when its own context is left,
            which may happen before or after the session closes.
        connect_kwargs
            Additional keyword arguments to pass to this module's `connect()`,
            which accepts `read_only` on top of the arguments of `sqlite3.connect()`.
        """
        db = cls(sqllog=sqllog)
        db._con = connect(path, **connect_kwargs)
        try:
            yield db
        finally:
            # Closing underneath a task that still holds the connection would cut off
            # its transaction, so the caller must have let every holder finish first.
            assert db._held is None
            # This close should flush any pending transactions to disk.
            db._con.close()
            db._con = None

    #
    # Transaction locking (asynchronous context manager)
    #

    async def _acquire(self, opened_transaction: bool) -> sqlite3.Connection:
        """Acquire exclusive access to the connection and return it.

        Parameters
        ----------
        opened_transaction
            True when the caller opens a transaction on the connection,
            False when it runs statements in autocommit mode.

        Raises
        ------
        RuntimeError
            When the calling task already holds the connection,
            or when the session was closed.
        """
        task = asyncio.current_task()
        held = self._held
        if held is not None and held.task is task:
            raise RuntimeError("Nested DBSession request detected within the same task.")
        await self._lock.acquire()
        # The connection is read after the wait,
        # because the session may have been closed while this task was waiting.
        if self._con is None:
            self._lock.release()
            raise RuntimeError("Database connection has already been closed.")
        self._held = _Held(task, self._con, opened_transaction)
        return self._con

    def _release(self) -> None:
        """Give up the exclusive access acquired by `_acquire()`."""
        self._held = None
        self._lock.release()

    def _require_transaction_con(self) -> sqlite3.Connection:
        """Return the connection of the transaction that the calling asyncio task is inside.

        Raises
        ------
        RuntimeError
            When the calling task is not inside a transaction,
            which includes the case where it holds the connection in autocommit mode.
        """
        held = self._held
        if held is None or held.task is not asyncio.current_task() or not held.opened_transaction:
            raise RuntimeError("No open transaction. Use `async with db:` first.")
        return held.con

    @contextlib.asynccontextmanager
    async def _autocommit_con(self) -> AsyncGenerator[sqlite3.Connection, None]:
        """Hold the connection exclusively, without opening a transaction.

        Statements run in autocommit mode on the yielded connection.
        `execute()` and `executemany()` are unavailable inside this context,
        because they require a transaction.
        """
        con = await self._acquire(opened_transaction=False)
        try:
            yield con
        finally:
            self._release()

    async def __aenter__(self) -> None:
        """Take exclusive access to the connection and open a transaction.

        The transaction is opened with `BEGIN IMMEDIATE`,
        so it takes the write lock right away instead of on the first write.
        Entering also bumps the transaction counter that labels the rows of the `--sqllog` file.

        Raises
        ------
        RuntimeError
            When the calling task already holds the connection,
            or when the session is closed.
        """
        con = await self._acquire(opened_transaction=True)
        try:
            con.execute("BEGIN IMMEDIATE")
            self._transaction_i += 1
        except Exception:
            self._release()
            raise

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Close the transaction and give up the exclusive access.

        The transaction is committed when the context is left without an exception,
        and rolled back otherwise.
        The exclusive access is given up in both cases.

        Raises
        ------
        RuntimeError
            When the context is left without an exception
            while the transaction is no longer open,
            which is what a stray `COMMIT` or an `executescript` inside the context leaves behind.
        """
        try:
            con = self._require_transaction_con()
            if exc is None:
                if not con.in_transaction:
                    raise RuntimeError(
                        "Transaction was closed mid-context (executescript or manual COMMIT?)."
                    )
                con.commit()
            else:
                con.rollback()
        finally:
            self._release()

    #
    # SQL execution
    #

    def _run(self, query: str, args: SQLArgs | Sequence[SQLArgs], *, many: bool) -> sqlite3.Cursor:
        """Run one statement on the connection of the current transaction.

        The execution is timed and logged when profiling is on,
        except when `executemany()` was given nothing to bind.

        Parameters
        ----------
        query
            The SQL statement to run.
        args
            The arguments of one execution,
            or the list of argument sets when `many` is True.
        many
            True to run the statement with `executemany()`, False to use `execute()`.

        Returns
        -------
        cursor
            The cursor of the statement that was run.
        """
        con = self._require_transaction_con()
        run = con.executemany if many else con.execute
        nrecords = len(args) if many else 1
        if self.sqllog is None or nrecords == 0:
            return run(query, args)
        # `execute()` and `executemany()` are the only callers of this method,
        # so the call site whose identity is logged is always exactly two frames up.
        key = QueryKey.from_frame(query, inspect.currentframe().f_back.f_back)
        with self.sqllog.time_execute(
            con,
            key,
            transaction_i=self._transaction_i,
            nrecords=nrecords,
            plan_args=args[0] if many else args,
        ):
            return run(query, args)

    def execute(self, query: str, args: SQLArgs | Iterable[Any] = ()) -> sqlite3.Cursor:
        """Execute an SQL statement with the given arguments.

        The arguments are a sequence for positional placeholders (`?`),
        or a mapping for named ones (`:name`).

        Raises
        ------
        RuntimeError
            When the calling task is not inside the transaction context of this session.
        TypeError
            When `args` is a string.
        """
        return self._run(query, _coerce_args(args), many=False)

    def executemany(
        self, query: str, seq_of_args: Iterable[SQLArgs | Iterable[Any]]
    ) -> sqlite3.Cursor:
        """Execute an SQL statement against all parameter sequences or mappings.

        Raises
        ------
        RuntimeError
            When the calling task is not inside the transaction context of this session.
        TypeError
            When one of the parameter sequences is a string.
        """
        return self._run(query, [_coerce_args(args) for args in seq_of_args], many=True)

    #
    # Schema initialization
    #

    async def apply_schema(
        self, application_id: int, schema_version: int, schema_scripts: Sequence[str]
    ) -> bool:
        """Bring the database up to the given SQL schema.

        A database that already holds a schema of a different version is wiped first,
        because the scripts only describe the current version.

        Parameters
        ----------
        application_id
            The application ID to set for the database.
        schema_version
            The schema version to set for the database.
        schema_scripts
            The SQL schema scripts to execute, in order, to set up the database.

        Returns
        -------
        is_fresh
            True if the database was empty before the scripts ran,
            either because it was new or because it was wiped over a schema version mismatch.
            False if it already contained the expected schema.

        Raises
        ------
        ValueError
            When the database was written by another application,
            which is detected through a mismatching application ID.
        """
        async with self._autocommit_con() as con:
            is_fresh = con.execute("SELECT count(*) FROM sqlite_master").fetchone()[0] == 0
            if not is_fresh:
                row = con.execute("PRAGMA application_id").fetchone()
                if row[0] != application_id:
                    raise ValueError("Invalid database application ID")
                row = con.execute("PRAGMA user_version").fetchone()
                if row[0] != schema_version:
                    _wipe_database(con)
                    is_fresh = True
            # The pragmas are written after the emptiness check and after the possible wipe,
            # because both read the state of the database as it was found on disk.
            con.execute(f"PRAGMA application_id = {application_id:d}")
            con.execute(f"PRAGMA user_version = {schema_version:d}")
            for script in schema_scripts:
                con.executescript(script)
            if is_fresh:
                # `VACUUM` cannot run inside a transaction,
                # which is why the exclusive mode is used here.
                con.execute("VACUUM")
        return is_fresh

    #
    # Database maintenance
    #

    async def reclaim_free_space(
        self, pages_per_chunk: int = 500, max_pages_to_free: int = 5000
    ) -> int:
        """Check the freelist and incrementally reclaim dead space on disk.

        This holds the connection in autocommit mode, so it must not be called inside a transaction.

        Parameters
        ----------
        pages_per_chunk
            The number of pages to reclaim per `incremental_vacuum` call.
            Nothing is reclaimed while the freelist is shorter than one chunk.
        max_pages_to_free
            A hard upper bound on the number of pages to reclaim in one call.
            Whatever is left over is picked up by the next call.

        Returns
        -------
        pages_freed
            The number of pages actually released by the incremental vacuum.
        """
        async with self._autocommit_con() as con:
            freelist_before = con.execute("PRAGMA freelist_count").fetchone()[0]
            # Reclaim in whole chunks, to avoid locking up or spiking disk I/O.
            for _ in range(min(freelist_before, max_pages_to_free) // pages_per_chunk):
                # The pragma frees one page per step, yielding a row without columns for each.
                # `executescript` steps through it exhaustively,
                # whereas `execute().fetchall()` on Python 3.11 treats the first such row
                # as the end of the result and leaves all but one page in place.
                con.executescript(f"PRAGMA incremental_vacuum({pages_per_chunk:d});")
            freelist_after = con.execute("PRAGMA freelist_count").fetchone()[0]
        return freelist_before - freelist_after

    async def reclaim_loop(
        self, stop_event: asyncio.Event, start_delay: float = 3.0, interval: float = 300.0
    ) -> None:
        """Periodically reclaim free disk space in the background.

        The loop exits cleanly when `stop_event` is set.
        An unexpected error is logged and ends the loop,
        so a database hiccup does not propagate out of this background task.

        Parameters
        ----------
        stop_event
            The event that ends the loop when it is set.
        start_delay
            The waiting time before the first reclamation.
        interval
            The waiting time between all later reclamations.
        """
        wait_time = start_delay
        try:
            while True:
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(stop_event.wait(), timeout=wait_time)
                if stop_event.is_set():
                    break
                await self.reclaim_free_space()
                # Only the first waiting time is the start delay.
                wait_time = interval
        except Exception:
            # `asyncio.CancelledError` derives from `BaseException`, not from `Exception`,
            # so cancellation of this background task is not swallowed here.
            logger.error("Error during database space reclamation", exc_info=True)
