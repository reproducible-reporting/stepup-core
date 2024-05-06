# StepUp Core provides the basic framework for the StepUp build tool.
# Copyright (C) 2024 Toon Verstraelen
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

import enum
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any, Self

import attrs
from path import Path

from .cascade import Node
from .hash import FileHash
from .utils import format_digest

if TYPE_CHECKING:
    from .workflow import Workflow


__all__ = ("FileState", "File")


class FileState(enum.Enum):
    # Declared static by the user: hand-made, don't overwrite or delete
    STATIC = 11

    # Declared as an output of a step, but step did not complete (succesfully) yet.
    PENDING = 12

    # Declared as an output of a step and step has completed.
    BUILT = 13

    # Declared static bu the user, but then deleted by the user.
    MISSING = 14

    # Declared as volatile output of a step:
    # - same cleaning as built files.
    # - cannot be used as input.
    # - no hashes are computed.
    VOLATILE = 15


@attrs.define
class File(Node):
    """A concrete file on the filesystem (may also be a directory)."""

    _path: Path = attrs.field(converter=Path)
    hash: FileHash = attrs.field(factory=FileHash.unknown)

    #
    # Getters
    #

    @property
    def path(self) -> Path:
        return self._path

    #
    # Initialization, serialization and formatting
    #

    @classmethod
    def key_tail(cls, data: dict[str, Any], strings: list[str] | None = None) -> str:
        """Subclasses must implement the key tail and accept both JSON or attrs dicts."""
        path = data.get("_path")
        if path is None:
            path = strings[data.get("p")]
        return path

    @classmethod
    def structure(cls, workflow: "Workflow", strings: list[str], data: dict) -> Self:
        state = FileState(data.pop("s"))
        file = cls(path=strings[data["p"]], hash=FileHash.structure(data["h"]))
        file.set_state(workflow, state)
        return file

    def unstructure(self, workflow: "Workflow", lookup: dict[str, int]) -> dict:
        return {
            "p": lookup[self._path],
            "s": self.get_state(workflow).value,
            "h": self.hash.unstructure(),
        }

    def format_properties(self, workflow: "Workflow") -> Iterator[tuple[str, str]]:
        yield "path", str(self._path)
        yield "state", str(self.get_state(workflow).name)
        if len(self.hash.digest) > 1:
            l1, l2 = format_digest(self.hash.digest)
            yield "digest", l1
            yield "", l2

    #
    # Overridden from base class
    #

    def recycle(self, workflow: "Workflow", old: Self | None):
        if old is not None:
            # Recycle hash
            self.hash = old.hash

    def orphan(self, workflow: "Workflow"):
        for step_key in workflow.get_consumers(self.key, kind="step"):
            step = workflow.get_step(step_key)
            step.make_pending(workflow)
            # When inputs of a step are orphaned,
            # amended information becomes unreliable because the orphaned
            # nodes may have been created by the step.
            step.clean_before_run(workflow)

    def cleanup(self, workflow: "Workflow"):
        if self._path.endswith("/"):
            workflow.dir_queue.put_nowait((True, self._path))
        state = self.get_state(workflow)
        if state == FileState.VOLATILE:
            workflow.to_be_deleted.append((self._path, None))
        elif state in (FileState.PENDING, FileState.BUILT) and self.hash.digest != b"u":
            workflow.to_be_deleted.append((self._path, self.hash))
        workflow.file_states.discard(self.key, insist=True)

    #
    # File state
    #

    def get_state(self, workflow: "Workflow") -> FileState:
        return workflow.file_states[self.key]

    def set_state(self, workflow: "Workflow", new_state: FileState):
        workflow.file_states[self.key] = new_state

    #
    # Run phase
    #

    def release_pending(self, workflow: "Workflow"):
        """Check all steps using this one as input and queue them if possible.

        In case of a directory, also notify the watcher by putting it on the dir_queue.
        """
        for step_key in sorted(workflow.get_consumers(self.key, kind="step")):
            step = workflow.get_step(step_key)
            step.validate_amended = True
            step.queue_if_appropriate(workflow)
        if self.path.endswith("/"):
            workflow.dir_queue.put_nowait((False, self.path))

    #
    # Watch phase
    #

    def watcher_deleted(self, workflow: "Workflow"):
        """Modify the graph to account for the fact this was deleted.

        Hashes are not removed in case the file is restored by the user with the same contents.
        """
        state = self.get_state(workflow)
        if state == FileState.MISSING:
            raise ValueError(f"Cannot delete a path that is already MISSING: {self._path}")
        if state == FileState.STATIC:
            self.set_state(workflow, FileState.MISSING)
        elif state == FileState.BUILT:
            self.set_state(workflow, FileState.PENDING)
            # Request rerun of creator
            workflow.get_step(workflow.get_creator(self.key)).make_pending(workflow)
        else:
            # No action needed when a PENDING or VOLATILE file is deleted.
            return
        # Make all consumers pending
        for step_key in workflow.get_consumers(self.key, kind="step"):
            workflow.get_step(step_key).make_pending(workflow)

    def watcher_updated(self, workflow: "Workflow"):
        """Modify the graph to account for the fact that this file changed on disk.

        Hashes are not updated until needed, to allow for reverting the file in its original
        form. (This is more common than one may thing, e.g. when switching Git branches.)
        """
        state = self.get_state(workflow)
        if state == FileState.MISSING:
            self.set_state(workflow, FileState.STATIC)
            state = FileState.STATIC
        if state == FileState.STATIC:
            # Make all consumers pending
            for step_key in workflow.get_consumers(self.key, kind="step"):
                workflow.get_step(step_key).make_pending(workflow, input_changed=True)
        else:
            # Make the creator pending again, as to make sure the file is rebuilt.
            creator_key = workflow.get_creator(self.key)
            if creator_key.startswith("step:"):
                workflow.get_step(creator_key).make_pending(workflow)

    def make_pending(self, workflow: "Workflow"):
        state = self.get_state(workflow)
        if state == FileState.BUILT:
            self.set_state(workflow, FileState.PENDING)
            for step_key in workflow.get_consumers(self.key, kind="step"):
                workflow.get_step(step_key).make_pending(workflow, input_changed=True)
        elif state != FileState.PENDING:
            raise ValueError(f"Cannot make file pending when its state is {state}")
