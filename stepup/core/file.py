# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""A `File` is StepUp's node for an input or output file of a step."""

import os
from collections.abc import Iterator

import attrs
from path import Path

from .enums import FileState
from .exceptions import PathError
from .hash import FileHash
from .trellis import Node
from .utils import format_digest

__all__ = ("REGULAR_OUTPUT_WHERE", "File")


# The test for "this file is a regular (non-volatile) output of a step",
# as an SQL fragment shared by every query that has to agree on what a regular output is.
# The dependency sinks of a step are its out_paths (PLANNED, BUILT or OUTDATED) and its
# vol_paths (VOLATILE), so ruling out VOLATILE leaves precisely the regular outputs.
#
# The fragment assumes three table aliases, which every caller must provide:
# `depo` for the dependency edge (its source is the producing step),
# `onode` for the output's node row, and `ofile` for its file row.
# Only the predicate is shared, not the joins around it:
# the callers drive their joins from different directions for query-planner reasons
# documented at each site.
REGULAR_OUTPUT_WHERE = f"NOT onode.detached AND ofile.state != {FileState.VOLATILE.value}"


FILE_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS file (
  node INTEGER PRIMARY KEY,
  state INTEGER NOT NULL
    CHECK(state >= {min(FileState)} AND state <= {max(FileState)}),
  hash TEXT,
  FOREIGN KEY (node) REFERENCES node(i) ON DELETE CASCADE,
  CHECK (
    state NOT IN ({FileState.CONFIRMED.value}, {FileState.BUILT.value}, {FileState.OUTDATED.value})
    OR hash IS NOT NULL
  ),
  CHECK (hash IS NULL OR json_valid(hash))
) WITHOUT ROWID;

-- The file_clear_hash trigger is defined using the following arguments:
-- * A hash is only meaningful for a file whose content is known and trusted
--   (CONFIRMED/BUILT/OUTDATED).
--   Null it out whenever the state moves to MISSING, PLANNED or VOLATILE,
--   so File.set_state does not have to special-case the reset itself.
-- * UNDECLARED and UNCONFIRMED keep a hash that came from a previous CONFIRMED state,
--   so FileHash.refreshed() can skip recomputing it when the file on disk is unchanged.
--   Both are needed for that: a path supplied as a step input before it is declared static
--   passes through UNDECLARED on its way back to UNCONFIRMED.
-- * UNCONFIRMED is only partly excluded:
--   a hash that came from BUILT or OUTDATED reflects what a step produced,
--   not a confirmed source's content,
--   so it must not survive a recycle into UNCONFIRMED.
--   That would let a leftover build product be silently adopted as a trusted source.
-- * UNDECLARED needs no such exclusion:
--   File.initialize_row carries a BUILT or OUTDATED state over instead of writing UNDECLARED,
--   and that is the only place UNDECLARED is ever written,
--   so an UNDECLARED row's hash can never have an output-role origin.
-- * Do not add `hash` to the `SET` clause of the upsert in File.initialize_row().
--   That would defeat the CONFIRMED-origin optimization this trigger carves the exception for.
CREATE TRIGGER IF NOT EXISTS file_clear_hash AFTER UPDATE OF state ON file
WHEN (
    NEW.state IN (
        {FileState.MISSING.value},
        {FileState.PLANNED.value},
        {FileState.VOLATILE.value}
    )
    OR (
        NEW.state = {FileState.UNCONFIRMED.value}
        AND OLD.state IN ({FileState.BUILT.value}, {FileState.OUTDATED.value})
    )
) AND NEW.hash IS NOT NULL
BEGIN
    UPDATE file SET hash = NULL WHERE node = NEW.node;
END;

-- UNDECLARED is the state of a file that never had a role,
-- so it can only occur on a node that nothing claims:
-- state = UNDECLARED implies the node is detached.
-- The converse does not hold
-- (a detached node keeps the state of its former life, see File.initialize_row),
-- so there is nothing to check in the other direction.
-- This invariant is deliberately guarded on the file side only.
-- Trellis.create's recycle path re-attaches a node (UPDATE node SET creator, detached)
-- before initialize_row writes its new state,
-- so an attached node briefly still carries the old UNDECLARED state.
-- A trigger on node would abort during that legitimate window;
-- a trigger on file never sees it, because no write to the file table happens inside it.
CREATE TRIGGER IF NOT EXISTS file_check_undeclared_detached_ins AFTER INSERT ON file
WHEN NEW.state = {FileState.UNDECLARED.value}
BEGIN
    SELECT RAISE(ABORT, 'an UNDECLARED file must be detached')
    FROM node WHERE node.i = NEW.node AND NOT node.detached;
END;

CREATE TRIGGER IF NOT EXISTS file_check_undeclared_detached_upd AFTER UPDATE OF state ON file
WHEN NEW.state = {FileState.UNDECLARED.value}
BEGIN
    SELECT RAISE(ABORT, 'an UNDECLARED file must be detached')
    FROM node WHERE node.i = NEW.node AND NOT node.detached;
END;
"""


@attrs.define
class File(Node):
    """A concrete file on the file system."""

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
        # These are not allowed but may pass "existence" checks.
        # Raise a `PathError` and not a plain `ValueError`:
        # these names can reach the director through a bad `static()` or `step()` argument,
        # i.e. they are user errors.
        if label in (".", "..", ""):
            raise PathError(f"Invalid file name: {label}")
        if label.endswith(os.sep):
            raise PathError(f"Invalid file name (directory): {label}")
        if label.endswith(("/.", "/..")):
            raise PathError(f"Invalid file name: {label}")
        return str(label)

    def initialize_row(self, state: FileState):  # type: ignore
        """Create extra information in the database about this node.

        Parameters
        ----------
        state
            The state to initialize the file with.
            A recycled node does not always end up in this state:
            one that was `BUILT` or `OUTDATED` before keeps its output state
            when it is recreated as `UNDECLARED` or `PLANNED`,
            with `BUILT` degrading to `OUTDATED` to force a rebuild.

        Notes
        -----
        A recycled node keeps its hash wherever that is safe
        (see the `file_clear_hash` trigger in `FILE_SCHEMA`),
        so a file that did not change on disk does not have to be hashed again
        and a step whose inputs and outputs are unchanged can still be skipped.
        """
        hash_json = None
        # UNDECLARED means that nothing declares the file (yet) and PLANNED that it must still
        # be built. Neither is a reason to forget that a step produced the file before,
        # so the old output state is restored instead of the requested one.
        if state in (FileState.UNDECLARED, FileState.PLANNED):
            sql = "SELECT state, hash FROM file WHERE node = ?"
            row = self.db.execute(sql, (self.i,)).fetchone()
            if row is not None and row[0] in (FileState.BUILT.value, FileState.OUTDATED.value):
                state = FileState(row[0])
                hash_json = row[1]
        # The upsert only assigns the state column when the row already exists,
        # so a recycled hash survives unless the file_clear_hash trigger nulls it afterward.
        # The old hash is nevertheless needed in the parameters below:
        # SQLite checks the file table's CHECK constraint against the literal VALUES(...),
        # even when the DO UPDATE branch never touches the hash column,
        # so a real hash must be supplied whenever the final state requires one.
        self.db.execute(
            "INSERT INTO file VALUES(:node, :state, :hash) "
            "ON CONFLICT DO UPDATE SET state = :state WHERE node = :node",
            {"node": self.i, "state": state.value, "hash": hash_json},
        )
        # Recycled BUILT files should be assumed to be out-of-date.
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

        The parent directory is queued whatever the state of the file,
        also when the file itself is not deleted (or is already gone).
        A directory is only removed when it is empty by the end of the cleanup pass,
        so this cannot take away a directory that something else still needs.
        """
        state = self.get_state()
        if state == FileState.VOLATILE:
            self.graph.to_be_deleted[self.path] = None
        elif state in (FileState.BUILT, FileState.OUTDATED):
            file_hash = self.get_hash()
            if not file_hash.is_unknown:
                self.graph.to_be_deleted[self.path] = file_hash
        self.graph.mark_dir_to_be_deleted(self.path.parent)

    #
    # Getters and setters
    #

    @property
    def path(self) -> Path:
        """The path of the file."""
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
        the hash is only ever written by the upsert in `initialize_row()`
        or by bulk SQL updates in `Workflow`, and it is nulled out by the `file_clear_hash` trigger
        whenever the new state may not keep the old hash.
        (See `FILE_SCHEMA` for the exact transitions.)
        """
        sql = "SELECT hash FROM file WHERE node = ?"
        row = self.db.execute(sql, (self.i,)).fetchone()
        return FileHash.from_json(row[0])
