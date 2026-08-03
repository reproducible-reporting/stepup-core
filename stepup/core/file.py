# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""A `File` is StepUp's node for an input or output file of a step."""

import os
from collections.abc import Iterator

import attrs
from path import Path

from .enums import FileState
from .hash import FileHash
from .trellis import Node
from .utils import format_digest

__all__ = ("File",)


FILE_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS file (
  node INTEGER PRIMARY KEY,
  state INTEGER NOT NULL
    CHECK(state >= {min(FileState)} AND state <= {max(FileState)}),
  hash TEXT,
  FOREIGN KEY (node) REFERENCES node(i) ON DELETE CASCADE,
  CHECK (
    state NOT IN ({FileState.STATIC.value}, {FileState.BUILT.value}, {FileState.OUTDATED.value})
    OR hash IS NOT NULL
  ),
  CHECK (hash IS NULL OR json_valid(hash))
) WITHOUT ROWID;

-- A hash is only meaningful for a file whose content is known and trusted
-- (STATIC/BUILT/OUTDATED); null it out whenever the state moves to MISSING, AWAITED or
-- VOLATILE, so File.set_state does not have to special-case the reset itself.
-- UNCONFIRMED is only partly excluded: a file being redeclared static keeps a hash that came
-- from a previous STATIC state, so FileHash.regen() can skip recomputing it when the file on
-- disk is unchanged. A hash that came from BUILT or OUTDATED reflects what a step produced,
-- not a confirmed source's content, so it must not survive a recycle into UNCONFIRMED -- that
-- would let a leftover build product be silently adopted as a trusted source. Do not add
-- `hash` to the `SET` clause of the upsert in File.initialize_row() -- that would defeat the
-- STATIC-origin optimization this trigger is carving the exception for.
CREATE TRIGGER IF NOT EXISTS file_clear_hash AFTER UPDATE OF state ON file
WHEN (
    NEW.state IN ({FileState.MISSING.value}, {FileState.AWAITED.value}, {FileState.VOLATILE.value})
    OR (
        NEW.state = {FileState.UNCONFIRMED.value}
        AND OLD.state IN ({FileState.BUILT.value}, {FileState.OUTDATED.value})
    )
) AND NEW.hash IS NOT NULL
BEGIN
    UPDATE file SET hash = NULL WHERE node = NEW.node;
END;
"""


@attrs.define
class File(Node):
    """A concrete file on the filesystem."""

    #
    # Override from base class
    #

    @classmethod
    def schema(cls) -> str | None:
        """Return node-specific SQL commands to initialize the database."""
        return FILE_SCHEMA

    @classmethod
    def adjust_label(cls, label: str, **kwargs) -> str:
        """Do not allow certain filenames, just as a sanity check to detect problems early."""
        # These are not allowed but may pass "existence" checks
        if label in (".", "..", ""):
            raise ValueError(f"Invalid file name: {label}")
        if label.endswith(os.sep):
            raise ValueError(f"Invalid file name (directory): {label}")
        if label.endswith(("/.", "/..")):
            raise ValueError(f"Invalid file name: {label}")
        return str(label)

    def initialize_row(self, state: FileState):  # type: ignore
        """Create extra information in the database about this node.

        Parameters
        ----------
        state
            The state to initialize the file with.
            If the file was previously `BUILT` or `OUTDATED`
            and is being (re)created as `AWAITED`,
            the previous state and hash are carried over instead
            (see the comments below for why).
        """
        hash_json = None
        # If the file was previously BUILT or OUTDATED, and created again as AWAITED,
        # it should copy that state (and hash).
        # Note: SQLite checks the file table's CHECK constraint against the literal VALUES(...),
        # even when the row already exists and the DO UPDATE branch never touches the hash column.
        # So a real hash must be supplied here whenever the final state requires one.
        #
        # The `SET state = :state` upsert below never touches the hash column on the reuse
        # (recycle) path, so a recycled file's previous hash survives unless the
        # file_clear_hash trigger nulls it afterward. This is what lets a redeclared
        # UNCONFIRMED file keep its old hash for FileHash.regen()'s cheap-skip check.
        if state == FileState.AWAITED:
            sql = "SELECT state, hash FROM file WHERE node = ?"
            row = self.db.execute(sql, (self.i,)).fetchone()
            if row is not None and row[0] in (FileState.BUILT.value, FileState.OUTDATED.value):
                state = FileState(row[0])
                hash_json = row[1]
        self.db.execute(
            "INSERT INTO file VALUES(:node, :state, :hash) "
            "ON CONFLICT DO UPDATE SET state = :state WHERE node = :node",
            {"node": self.i, "state": state.value, "hash": hash_json},
        )
        # If the state is BUILT, mark it as OUTDATED to force a rebuild.
        if state == FileState.BUILT:
            self.graph.mark_file_outdated(self)

    def validate_row(self):
        """Validate that extra information about this node is present in the database."""
        row = self.db.execute("SELECT 1 FROM file WHERE node = ?", (self.i,)).fetchone()
        if row is None:
            raise ValueError(f"File node {self.key()} has no row in the file table.")

    def format_properties(self) -> Iterator[tuple[str, str]]:
        """Iterate over key-value pairs that represent the properties of the node."""
        yield "state", str(self.get_state().name)
        file_hash = self.get_hash()
        if not file_hash.is_unknown:
            yield "digest", format_digest(file_hash.digest)

    def after_lost_product(self):
        """Always raise, since a file node never has products and thus never loses one."""
        raise AssertionError("A file node never has products, so it cannot lose one.")

    def before_delete(self):
        """Perform a cleanup right before the detached node is deleted from the graph.

        The row in the file table is removed automatically by `ON DELETE CASCADE`
        when the node row is deleted; here we only queue the on-disk file for deletion.
        """
        state = self.get_state()
        if state == FileState.VOLATILE:
            self.graph.to_be_deleted[self.path] = None
        elif state in (FileState.BUILT, FileState.OUTDATED):
            file_hash = self.get_hash()
            if not file_hash.is_unknown:
                self.graph.to_be_deleted[self.path] = file_hash

    #
    # Getters and setters
    #

    @property
    def path(self) -> Path:
        return Path(self.label)

    def get_state(self) -> FileState:
        """Return the current state of the file."""
        row = self.db.execute("SELECT state FROM file WHERE node = ?", (self.i,)).fetchone()
        return FileState(row[0])

    def set_state(self, state: FileState):
        """Update the state of the file."""
        self.db.execute("UPDATE file SET state = ? WHERE node = ?", (state.value, self.i))

    def get_hash(self) -> FileHash:
        """Return the hash of the file.

        There is no corresponding `set_hash`:
        the hash is only ever written by the upsert in `initialize()`
        or by bulk SQL updates in `Workflow`,
        and it is nulled out by the `file_clear_hash` trigger
        (see `FILE_SCHEMA`) whenever the state moves to a hash-less state.
        """
        sql = "SELECT hash FROM file WHERE node = ?"
        row = self.db.execute(sql, (self.i,)).fetchone()
        return FileHash.from_json(row[0])
