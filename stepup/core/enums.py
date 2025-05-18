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

from enum import Enum, Flag, auto

__all__ = ("Change", "DirWatch", "FileState", "Mandatory", "ReturnCode", "StepState")


class ReturnCode(Flag):
    INTERNAL = auto()
    """Exception raised, not related to failing steps in the workflow"""

    FAILED = auto()
    """Some steps failed."""

    PENDING = auto()
    """Some steps remained pending."""

    RUNNABLE = auto()
    """Some steps are runnable. Stopped early due to shutdown, drain, etc."""


class FileState(Enum):
    """State of a file in the StepUp workflow.

    STATIC and BUILT files are ready to be used as inputs.
    AWAITED, MISSING, VOLATILE and OUTDATED files are not.

    The availability and purpose of file hashes depend on the file state:

    - File hashes are available for STATIC, OUTDATED and BUILT files.
      They are not for AWAITED, MISSING and VOLATILE files.

    - In case of STATIC files, the hash is computed when the file is declared static,
      or when StepUp starts and checks the state of all files in the database.
      The hashes of BUILT files are computed when the step completes.
      OUTDATED files maintain the same hash from their BUILT state.
    """

    MISSING = 11
    """A file declared static by the user, but then deleted by the user."""

    STATIC = 12
    """A file that is declared static by the user.

    These are user-provided and will never be overwritten are deleted by StepUp.
    """

    AWAITED = 13
    """A file that has never been seen or built before.

    If it exists, it was created externally and not (yet) known to be static or built.
    """

    BUILT = 14
    """An output of a step and step has completed."""

    OUTDATED = 15
    """An old output of a step that is no longer up-to-date."""

    VOLATILE = 16
    """A file declared as volatile output of a step.

    This means the following:

    - Volatile files are cleaned up just like built files.
    - Volatile files cannot be used as input.
    - No hashes are computed for volatile files.
    - They can change when a step is repeated with the same inputs.
    """


class StepState(Enum):
    PENDING = 21
    """The step still needs to be executed."""

    QUEUED = 22
    """The step is handed over to the scheduler and will be executed soon."""

    RUNNING = 23
    """The step is being executed by a worker."""

    SUCCEEDED = 24
    """The step has completed successfully (exit code 0)."""

    FAILED = 25
    """The step has failed (exit code non-zero)."""


class Mandatory(Enum):
    YES = 31
    """The step must be executed (default)."""

    REQUIRED = 32
    """The step is optional but (indirectly) required by a mandatory step."""

    NO = 33
    """The step is optional and not required by another mandatory or required step."""


class Change(Enum):
    UPDATED = 41
    """A file on disk has been added or changed."""

    DELETED = 42
    """A file on disk has been deleted."""


class DirWatch(Enum):
    """Flag to change the watched directories through the Workflow.dir_queue."""

    START = 51
    """Request to start watching a directory."""

    STOP = 52
    """Request to stop watching a directory."""
