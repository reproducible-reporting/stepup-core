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
"""Wrapper for SQLite3 functionality."""

import asyncio
import contextlib
import logging
import os
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Self

import attrs

from .path import StrPath, coerce_path

__all__ = (
    "DBSession",
    "UInt64",
    "connect",
    "escape_like_pattern",
)


logger = logging.getLogger(__name__)


class UInt64(int):
    """A wrapper to tell SQLite this int should be treated as an unsigned 64-bit value."""

    MAX_SIGNED_64 = 2**63 - 1
    MAX_WRAPAROUND_64 = 2**64
    MAX_UNSIGNED_64 = MAX_WRAPAROUND_64 - 1

    @staticmethod
    def adapt(val: Self) -> int:
        if not (0 <= val <= UInt64.MAX_UNSIGNED_64):
            raise ValueError(f"Value {val} out of UINT64 range")
        return val - UInt64.MAX_WRAPAROUND_64 if val > UInt64.MAX_SIGNED_64 else val

    @staticmethod
    def convert(val: bytes) -> Self:
        val = int(val)
        if val < 0:
            val += UInt64.MAX_WRAPAROUND_64
        return UInt64(val)


sqlite3.register_adapter(UInt64, UInt64.adapt)
sqlite3.register_converter("UINT64", UInt64.convert)


def escape_like_pattern(pattern: str) -> str:
    """Escape a string for use in a LIKE pattern."""
    return pattern.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


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

    Notes
    -----
    The following deviations from the default settings are used:

    - Types can be detected from declared column types,
      which allows us to use the custom UINT64 type for file inodes.
    - The `cached_statements` parameter is set to a large value to improve
      performance when executing many similar statements.
    - Foreign key enforcement is enabled, which is required for the `ON DELETE CASCADE`
      cleanup of satellite rows. This is a per-connection setting (not stored in the
      database file), so it must be set on every connection.
    - The journal mode is set to WAL (Write-Ahead Logging) to allow concurrent reads and writes.
    - The synchronous mode is set to OFF to improve performance,
      at the cost of potential data loss in the event of a hard crash.
      This is ok because a few lost transactions are not critical for StepUp,
      as long as the database is not fully corrupted.
    - The auto_vacuum mode is set to INCREMENTAL to allow incremental vacuuming of the database.
    """
    kwargs = kwargs.copy()
    kwargs.setdefault("cached_statements", 1024)
    kwargs.setdefault("detect_types", sqlite3.PARSE_DECLTYPES)
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
        con = sqlite3.connect(coerce_path(path), **kwargs)
        con.isolation_level = None
        con.execute("PRAGMA foreign_keys = ON")
        con.execute("PRAGMA journal_mode = WAL")
        con.execute("PRAGMA synchronous = OFF")
        con.execute("PRAGMA auto_vacuum = INCREMENTAL")
    return con


@attrs.define
class DBSession:
    """Manages SQLite lifetime (via sync context) and exclusive access (via async context)."""

    # Store parameters for opening the connection,
    # which is in itself kept private to the DBSession instance.
    db_path: str | os.PathLike[str] = attrs.field()
    connect_kwargs: dict[str, Any] = attrs.field(factory=dict)

    _con: sqlite3.Connection | None = attrs.field(init=False, default=None)
    _lock: asyncio.Lock = attrs.field(factory=asyncio.Lock, init=False)
    _cv: ContextVar[sqlite3.Connection | None] = attrs.field(
        factory=lambda: ContextVar("con_cv", default=None), init=False
    )

    #
    # Application lifecycle (Synchronous Context Manager)
    #

    def __attrs_post_init__(self) -> None:
        """Open the connection from the start."""
        self._con = connect(self.db_path, **self.connect_kwargs)

    def _close(self) -> None:
        """Close the database connection."""
        if self._con:
            # This close should flush any pending transactions to disk.
            self._con.close()
            self._con = None

    @classmethod
    @contextmanager
    def open(cls, db_path: str | os.PathLike[str], **connect_kwargs: Any) -> Iterator[Self]:
        """Open a database connection and yield a DBSession instance for exclusive access."""
        db = cls(db_path, connect_kwargs)
        try:
            yield db
        finally:
            db._close()

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
        """Connection, only accessible if the calling asyncio task currently holds the lock."""
        con = self._cv.get()
        if con is None:
            raise RuntimeError(
                "No active database connection. You must acquire the DBSession first."
            )
        return con

    def execute(self, sql: str, args: Iterable[Any] = ()) -> sqlite3.Cursor:
        """Execute an SQL statement with the given arguments."""
        con = self._take_con()
        return con.execute(sql, args)

    def executemany(self, sql: str, seq_of_args: Iterable[Iterable[Any]]) -> sqlite3.Cursor:
        """Execute an SQL statement against all parameter sequences or mappings."""
        con = self._take_con()
        return con.executemany(sql, seq_of_args)

    async def initialize(
        self, application_id: int, schema_version: int, schema_blobs: list[str]
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
                # Safe to execute inside an isolated initialization run
                self._con.execute("VACUUM")
        finally:
            self._lock.release()
        return empty

    #
    # Database maintenance (incremental vacuuming)
    #

    def clean_free_space(self, chunk_size: int = 500, max_pages_to_free: int = 5000) -> int:
        """Checks the freelist and incrementally reclaims dead space from the disk.

        Must be called within an active transaction context (e.g., inside an async with block).
        Returns the total number of pages freed.
        """
        con = self._take_con()

        # 1. Query how many empty pages SQLite is holding onto
        freelist_count = con.execute("PRAGMA freelist_count").fetchone()[0]

        # Only clean up if the freelist is worth the effort (e.g., larger than our chunk size)
        if freelist_count < chunk_size:
            return 0

        pages_freed = 0
        pages_target = min(freelist_count, max_pages_to_free)

        # 2. Vacuum in small chunks so we don't lock up or spike disk I/O
        while pages_freed < pages_target:
            # We must exhaustively step through the incremental_vacuum pragma result
            con.execute("PRAGMA incremental_vacuum(?)", (chunk_size,)).fetchall()
            pages_freed += chunk_size

        return pages_freed

    async def database_maintenance_loop(
        self, stop_event: asyncio.Event, start_delay: float = 3.0, interval: float = 300.0
    ) -> None:
        """Background loop that periodically reclaims free disk space.

        Exits cleanly when the provided stop_event is set.
        """
        wait_time = start_delay
        while not stop_event.is_set():
            try:
                # Use a short timeout step-loop or wait for the event to minimize shutdown lag
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
                # Handle standard cooperative task cancellation gracefully
                break
            except BaseException:
                # Safeguard: Log or handle exceptions so a random database hickup
                # doesn't completely crash your entire application loop structure.
                logger.error("Error during database maintenance loop", exc_info=True)
                # Exit the loop on error to avoid repeated failures
                return


def _wipe_database(con: sqlite3.Connection):
    """Removes all tables and indexes from an SQLite database.

    This function is not to be used inside a transaction,
    because it temporarily disables foreign key constraints.
    """
    try:
        # Temporarily disable foreign key constraints
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
        # Restore foreign key constraints
        con.execute("PRAGMA foreign_keys = ON")
