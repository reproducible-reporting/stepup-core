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
"""A `File` is StepUp's node for an input or output file of a step."""

import logging
import os
from collections.abc import Iterator

import attrs
from path import Path

from .enums import FileState
from .hash import FileHash
from .trellis import Node
from .utils import format_digest

__all__ = ("File",)


logger = logging.getLogger(__name__)


FILE_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS file (
  node INTEGER PRIMARY KEY,
  state INTEGER NOT NULL CHECK(state >= 11 AND state <= 16),
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
CREATE TRIGGER IF NOT EXISTS file_clear_hash AFTER UPDATE OF state ON file
WHEN NEW.state IN ({FileState.MISSING.value}, {FileState.AWAITED.value}, {FileState.VOLATILE.value})
     AND NEW.hash IS NOT NULL
BEGIN
    UPDATE file SET hash = NULL WHERE node = NEW.node;
END;
"""


@attrs.define
class File(Node):
    """A concrete file on the filesystem (may also be a directory)."""

    #
    # Override from base class
    #

    @classmethod
    def schema(cls) -> str | None:
        """Return node-specific SQL commands to initialize the database."""
        return FILE_SCHEMA

    @classmethod
    def create_label(cls, label: str, **kwargs):
        """Do not allow certain filenames, just as a sanity check to detect problems early."""
        # These are not allowed but may pass "existence" checks
        if label in (".", "..", ""):
            raise ValueError(f"Invalid file name: {label}")
        if label.endswith(os.sep):
            raise ValueError(f"Invalid file name (directory): {label}")
        if label.endswith(("/.", "/..")):
            raise ValueError(f"Invalid file name: {label}")
        return str(label)

    def initialize(self, state: FileState):  # type: ignore
        """Create extra information in the database about this node."""
        file_hash = FileHash.unknown()
        # If the file was previously BUILT or OUTDATED, and created again as AWAITED,
        # it should copy that state
        if state == FileState.AWAITED:
            sql = "SELECT state, hash FROM file WHERE node = ?"
            row = self.db.execute(sql, (self.i,)).fetchone()
            if row is not None and row[0] in (FileState.BUILT.value, FileState.OUTDATED.value):
                state = FileState(row[0])
                file_hash = FileHash.from_json(row[1])
        if file_hash.is_unknown and state in (
            FileState.STATIC,
            FileState.BUILT,
            FileState.OUTDATED,
        ):
            raise ValueError(f"Cannot create a {state.name} file without a hash: {self.path}")
        # Add/update row in the file table.
        self.db.execute(
            "INSERT INTO file VALUES(:node, :state, :hash) "
            "ON CONFLICT DO UPDATE SET state = :state, hash = :hash WHERE node = :node",
            {"node": self.i, "state": state.value, "hash": file_hash.to_json()},
        )
        # If the state is BUILT, mark it as OUTDATED to force a rebuild.
        if state == FileState.BUILT:
            self.mark_outdated()

    def validate(self):
        """Validate extra information about this node is present in the database."""
        row = self.db.execute("SELECT 1 FROM file WHERE node = ?", (self.i,)).fetchone()
        if row is None:
            raise ValueError(f"File node {self.key()} has no row in the file table.")

    def format_properties(self) -> Iterator[tuple[str, str]]:
        """Iterate over key-value pairs that represent the properties of the node."""
        yield "state", str(self.get_state().name)
        file_hash = self.get_hash()
        if len(file_hash.digest) > 1:
            yield "digest", format_digest(file_hash.digest)

    def clean(self):
        """Perform a cleanup right before the detached node is removed from the graph.

        The row in the file table is removed automatically by `ON DELETE CASCADE`
        when the node row is deleted; here we only queue the on-disk file for deletion.
        """
        state = self.get_state()
        if state == FileState.VOLATILE:
            self.graph.to_be_deleted.append((self.path, None))
        elif state in (FileState.BUILT, FileState.OUTDATED):
            file_hash = self.get_hash()
            if not file_hash.is_unknown:
                self.graph.to_be_deleted.append((self.path, file_hash))

    def give_up(self):
        """Clean up a detached node because it loses a product node."""
        raise AssertionError("A file node never has products, so it cannot be detached.")

    #
    # Getters and setters
    #

    @property
    def path(self) -> Path:
        return Path(self.label)

    def get_state(self) -> FileState:
        row = self.db.execute("SELECT state FROM file WHERE node = ?", (self.i,)).fetchone()
        return FileState(row[0])

    def set_state(self, state: FileState):
        self.db.execute("UPDATE file SET state = ? WHERE node = ?", (state.value, self.i))

    def get_hash(self) -> FileHash:
        sql = "SELECT hash FROM file WHERE node = ?"
        row = self.db.execute(sql, (self.i,)).fetchone()
        return FileHash.from_json(row[0])

    #
    # Build phase
    #

    def completed(self):
        """Check and if necessary, mark all consumer steps pending."""
        # Local import to avoid cyclic imports.
        from .step import Step  # noqa: PLC0415

        state = self.get_state()
        if state in [FileState.STATIC, FileState.BUILT]:
            logger.info("Completed %s file: %s", state, self.path)
            for step in self.consumers(Step, include_detached=True):
                step.mark_pending()

    #
    # Watch phase
    #

    def externally_deleted(self):
        """Modify the graph to account for the fact this file was deleted.

        File states and hashes have already been updated before this method is called.
        """
        state = self.get_state()
        logger.info("Externally deleted %s file: %s", state, self.path)

        if state == FileState.STATIC:
            self.set_state(FileState.MISSING)
            state = FileState.MISSING
        elif state in (FileState.BUILT, FileState.OUTDATED):
            self.set_state(FileState.AWAITED)
            state = FileState.AWAITED

        if state == FileState.AWAITED:
            # Request rerun of creator
            creator = self.creator()
            if creator is not None and creator.kind() == "step":
                creator.mark_pending()
        if state != FileState.VOLATILE:
            # Make all consumers pending.
            # Local import to avoid cyclic imports.
            from .step import Step  # noqa: PLC0415

            for step in self.consumers(Step):
                step.mark_pending()
            for file in self.consumers(File):
                file.externally_deleted()

    def externally_updated(self):
        """Modify the graph to account for the external changes to this file.

        File states and hashes have already been updated before this method is called.
        """
        state = self.get_state()
        if state == FileState.STATIC:
            # Mark all consumers pending.
            # Local import to avoid cyclic imports.
            from .step import Step  # noqa: PLC0415

            for step in self.consumers(Step):
                step.mark_pending()
        elif state == FileState.AWAITED:
            # Mark the creator pending, as to make sure the file is rebuilt.
            creator = self.creator()
            if creator is not None and creator.kind() == "step":
                creator.mark_pending()

    def mark_outdated(self):
        state = self.get_state()
        if state == FileState.BUILT:
            logger.info("Mark %s file OUTDATED: %s", state, self.path)
            self.set_state(FileState.OUTDATED)
            # Local import to avoid cyclic imports.
            from .step import Step  # noqa: PLC0415

            for step in self.consumers(Step, include_detached=True):
                step.mark_pending()
        elif state != FileState.OUTDATED:
            raise ValueError(f"Cannot make file outdated when its state is {state}")
