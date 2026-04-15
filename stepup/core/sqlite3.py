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

import contextlib
import sqlite3
from collections.abc import Iterator
from typing import Self

__all__ = ("UInt64", "connect", "copy_db_in_memory")


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


def connect(path: str, **kwargs) -> sqlite3.Connection:
    """Connect to a SQLite database, with the appropriate settings for StepUp.

    The following deviations from the default settings are used:

    - Types can be detected from column names,
      which allows us to use the custom UINT64 type for file inodes.
    - The `cached_statements` parameter is set to a large value to improve
      performance when executing many similar statements.
    """
    my_kwargs = {"cached_statements": 1024, "detect_types": sqlite3.PARSE_COLNAMES}
    my_kwargs.update(kwargs)
    return sqlite3.connect(path, **my_kwargs)


@contextlib.contextmanager
def copy_db_in_memory(path_db) -> Iterator[sqlite3.Connection]:
    """Copy an SQLite database into memory and yield the connection."""
    dst = connect(":memory:")
    try:
        src = connect(path_db)
        try:
            src.backup(dst)
        finally:
            src.close()
        yield dst
    finally:
        dst.close()
