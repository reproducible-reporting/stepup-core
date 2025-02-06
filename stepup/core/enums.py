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
"""Enumerate objects used in other StepUp modules."""

import enum

__all__ = ("Change", "DirWatch", "FileState", "Mandatory", "ReturnCode", "StepState")


class ReturnCode(enum.Enum):
    # All (mandatory) steps completed successfully.
    SUCCESS = 0

    # Exception raised, not related to steps in the workflow
    INTERNAL = 1

    # Some steps failed.
    FAILED = 2

    # Some steps remained pending.
    PENDING = 3


class FileState(enum.Enum):
    # Declared static by the user: hand-made, don't overwrite or delete
    STATIC = 11

    # A file that has never been seen or built before.
    # If it exists, it was created externally and not (yet) known to be static or built.
    AWAITED = 12

    # An output of a step and step has completed.
    BUILT = 13

    # Declared static by the user, but then deleted by the user.
    MISSING = 14

    # Declared as volatile output of a step:
    # - same cleaning as built files.
    # - cannot be used as input.
    # - no hashes are computed.
    VOLATILE = 15

    # An old output of a step that is no longer up-to-date.
    OUTDATED = 16


class StepState(enum.Enum):
    # Step still needs to be executed.
    PENDING = 21

    # The step is submitted to the scheduler and will be executed soon.
    QUEUED = 22

    # The step is being executed by a worker.
    RUNNING = 23

    # The step has completed successfully (exit code 0).
    SUCCEEDED = 24

    # The step has failed (exit code non-zero).
    FAILED = 25


class Mandatory(enum.Enum):
    # The step must be executed (default).
    YES = 31

    # The step is optional but (indirectly) required by a mandatory step.
    REQUIRED = 32

    # The step is optional and not required by another mandatory or required step.
    NO = 33


class Change(enum.Enum):
    # A file on disk has been added or changed.
    UPDATED = 41

    # A file on disk has been deleted.
    DELETED = 42


class DirWatch(enum.Enum):
    """Flag to change the watched directories through the Workflow.dir_queue."""

    # Request to start watching a directory.
    START = 51

    # Request to stop watching a directory.
    STOP = 52
