# StepUp Core provides the basic framework for the StepUp build tool.
# © 2024–2025 Toon Verstraelen
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
from collections.abc import Iterator
from typing import TYPE_CHECKING

import attrs
from path import Path

from .cascade import Node
from .enums import DirWatch, FileState
from .hash import FileHash
from .utils import format_digest

if TYPE_CHECKING:
    from .workflow import Workflow


__all__ = ("File",)


logger = logging.getLogger(__name__)


FILE_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS file (
  node INTEGER PRIMARY KEY,
  state INTEGER NOT NULL CHECK(state >= 11 AND state <= 16),
  digest BLOB NOT NULL,
  mode INTEGER NOT NULL CHECK(mode >= 0),
  mtime REAL NOT NULL CHECK(mtime >= 0),
  size INTEGER NOT NULL CHECK(size >= 0),
  inode INTEGER NOT NULL,
  FOREIGN KEY (node) REFERENCES node(i),
  CHECK (
    state IN ({FileState.MISSING.value}, {FileState.AWAITED.value}, {FileState.VOLATILE.value}) OR
    (digest != X'75' AND mtime != 0 AND inode != 0) OR
    (digest == X'64' AND mode != 0 AND mtime = 0 AND size = 0 AND inode = 0)
  ),
  CHECK (
    state IN ({FileState.STATIC.value}, {FileState.BUILT.value}, {FileState.OUTDATED.value}) OR
    (digest = X'75' AND mode = 0 AND mtime = 0 AND size = 0 AND inode = 0)
  )
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS file_state ON file(state);
"""


@attrs.define
class File(Node):
    """A concrete file on the filesystem (may also be a directory)."""

    @property
    def workflow(self) -> "Workflow":
        return self.cascade

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
        if label.endswith(("/.", "/..")):
            raise ValueError(f"Invalid file name: {label}")
        return str(label)

    def initialize(self, state: FileState):  # type: ignore
        """Create extra information in the database about this node."""
        digest = b"u"
        mode = 0
        mtime = 0.0
        size = 0
        inode = 0
        # If the file was previously BUILT or OUTDATED, and created again as AWAITED,
        # it should copy that state
        if state == FileState.AWAITED:
            sql = "SELECT state, digest, mode, mtime, size, inode FROM file WHERE node = ?"
            row = self.con.execute(sql, (self.i,)).fetchone()
            if row is not None and row[0] in (FileState.BUILT.value, FileState.OUTDATED.value):
                state = FileState(row[0])
                digest, mode, mtime, size, inode = row[1:]
        if digest == b"u" and state in (FileState.STATIC, FileState.BUILT, FileState.OUTDATED):
            raise ValueError(f"Cannot create a {state.name} file without a hash: {self.path}")
        # Add/update row in the file table.
        self.con.execute(
            "INSERT INTO file VALUES(:node, :state, :digest, :mode, :mtime, :size, :inode) "
            "ON CONFLICT DO UPDATE SET state = :state, digest = :digest, mode = :mode, "
            "mtime = :mtime, size = :size, inode = :inode WHERE node = :node",
            {
                "node": self.i,
                "state": state.value,
                "digest": digest,
                "mode": mode,
                "mtime": mtime,
                "size": size,
                "inode": inode,
            },
        )
        # If the state is BUILT, mark it as OUTDATED to force a rebuild.
        if state == FileState.BUILT:
            self.mark_outdated()

    def validate(self):
        """Validate extra information about this node is present in the database."""
        row = self.con.execute("SELECT 1 FROM file WHERE node = ?", (self.i,)).fetchone()
        if row is None:
            raise ValueError(f"File node {self.key()} has no row in the file table.")

    def format_properties(self) -> Iterator[tuple[str, str]]:
        """Iterate over key-value pairs that represent the properties of the node."""
        yield "state", str(self.get_state().name)
        file_hash = self.get_hash()
        if len(file_hash.digest) > 1:
            l1, l2 = format_digest(file_hash.digest)
            yield "digest", l1
            yield "", l2

    def clean(self):
        """Perform a cleanup right before the orphaned node is removed from the graph."""
        if self.path.endswith("/"):
            self.workflow.dir_queue.put_nowait((DirWatch.STOP, self.path))
        state = self.get_state()
        if state == FileState.VOLATILE:
            self.workflow.to_be_deleted.append((self.path, None))
        elif state in (FileState.BUILT, FileState.OUTDATED):
            file_hash = self.get_hash()
            if file_hash.digest != b"u":
                self.workflow.to_be_deleted.append((self.path, file_hash))
        self.con.execute("DELETE FROM file WHERE node = ?", (self.i,))

    def add_supplier(self, supplier: Node) -> int:
        """Add a supplier-consumer relation.

        Parameters
        ----------
        supplier
            Other node that supplies to thise node.

        Returns
        -------
        idep
            The identifier in the dependency table.
        """
        idep = super().add_supplier(supplier)
        if supplier.kind() == "step":
            supplier.make_required()
        return idep

    def del_suppliers(self, suppliers: list[Node] | None = None):
        """Delete given suppliers.

        Without arguments, all suppliers of the current node are deleted.
        """
        # Get a list of suppliers to process if needed
        _suppliers = suppliers
        if suppliers is None:
            _suppliers = list(self.suppliers(include_orphans=True))
        super().del_suppliers(suppliers)
        for supplier in _suppliers:
            if supplier.kind() == "step":
                supplier.undo_required()

    def detach(self):
        """Clean up an orphaned node because it loses a product node."""
        raise AssertionError("A file node never has products, so it cannot be detaced.")

    #
    # Getters and setters
    #

    @property
    def path(self) -> Path:
        return Path(self.label)

    def get_state(self) -> FileState:
        row = self.con.execute("SELECT state FROM file WHERE node = ?", (self.i,)).fetchone()
        return FileState(row[0])

    def set_state(self, state: FileState):
        if state in (FileState.MISSING, FileState.AWAITED, FileState.VOLATILE):
            sql = (
                "UPDATE file SET state = ?, digest = X'75', mode = 0, mtime = 0, "
                "size = 0, inode = 0 WHERE node = ?"
            )
        else:
            sql = "UPDATE file SET state = ? WHERE node = ?"
        self.con.execute(sql, (state.value, self.i))

    def get_hash(self) -> FileHash:
        sql = "SELECT digest, mode, mtime, size, inode FROM file WHERE node = ?"
        row = self.con.execute(sql, (self.i,)).fetchone()
        return FileHash(*row)

    #
    # Run phase
    #

    def release_pending(self):
        """Check all steps using this one as input and queue them if possible.

        In case of a directory, also notify the watcher by putting it on the dir_queue.
        """
        from .step import Step

        if self.get_state() in [FileState.STATIC, FileState.BUILT]:
            for step in self.consumers(Step):
                step.set_validate_amended()
                step.queue_if_appropriate()
            if self.path.endswith("/"):
                self.workflow.dir_queue.put_nowait((DirWatch.START, self.path))

    #
    # Watch phase
    #

    def externally_deleted(self):
        """Modify the graph to account for the fact this file was deleted.

        File states and hashes have already been updated before this method is called.
        """
        if self.path.endswith("/"):
            self.workflow.dir_queue.put_nowait((DirWatch.STOP, self.path))
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
            self.creator().mark_pending()
        if state != FileState.VOLATILE:
            # Make all consumers pending
            from .step import Step

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
            # Mark all consumers pending
            from .step import Step

            for step in self.consumers(Step):
                step.mark_pending(input_changed=True)
        elif state == FileState.AWAITED:
            # Mark the creator pending, as to make sure the file is rebuilt.
            creator = self.creator()
            if creator.kind() == "step":
                creator.mark_pending()

    def mark_outdated(self):
        state = self.get_state()
        if state == FileState.BUILT:
            logger.info("Mark %s file OUTDATED: %s", state, self.path)
            self.set_state(FileState.OUTDATED)
            from .step import Step

            for step in self.consumers(Step):
                step.mark_pending(input_changed=True)
        elif state != FileState.OUTDATED:
            raise ValueError(f"Cannot make file oudated when its state is {state}")
