# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Enumerate objects used in other StepUp modules."""

from enum import Flag, IntEnum, auto

__all__ = (
    "REGULAR_OUTPUT_STATES",
    "TARGET_FORBIDDEN_STATES",
    "Change",
    "FileState",
    "HashUpdateCause",
    "Need",
    "ReturnCode",
    "StepState",
)


class ReturnCode(Flag):
    INTERNAL = auto()
    """Exception raised, not related to failing steps in the workflow"""

    FAILED = auto()
    """Some steps failed."""

    PENDING = auto()
    """Some steps remained pending."""

    RUNNABLE = auto()
    """Some steps are runnable. Stopped early due to shutdown, drain, etc."""

    NOTPRODUCED = auto()
    """Some targets were not produced by any step in the workflow."""

    ONHOLD = auto()
    """The scheduler is on hold. Pending steps are not reported."""


class FileState(IntEnum):
    """State of a file in the StepUp workflow.

    STATIC and BUILT files are ready to be used as inputs.
    UNCONFIRMED, MISSING, AWAITED, VOLATILE and OUTDATED files are not.

    The availability and purpose of file hashes depend on the file state:

    - File hashes are available for STATIC, OUTDATED and BUILT files.
    - They are not for AWAITED, MISSING and VOLATILE files.
    - They may be present for UNCONFIRMED files, but are not guaranteed to be up-to-date.

    Hashes are computed for STATIC files when they are declared.
    Such files are first declared as UNCONFIRMED and then evolve to STATIC or MISSING,
    depending on the confirmation outcome at the client side where the file was declared.

    The hashes of BUILT files are computed when the step completes.
    OUTDATED files maintain the same hash from their BUILT state.
    """

    UNCONFIRMED = 11
    """A file that is declared static, but whose existence (and hash) needs to be confirmed."""

    MISSING = 12
    """A file declared static, but confirmed to be absent
    (never present, or deleted by the user).
    """

    STATIC = 13
    """A file that is declared static by the user.

    These are user-provided and will never be overwritten or deleted by StepUp.
    """

    AWAITED = 14
    """A file that has never been seen or built before.

    If it exists, it was created externally and not (yet) known to be static or built.
    """

    BUILT = 15
    """An output of a step and step has completed."""

    OUTDATED = 16
    """An old output of a step that is no longer up-to-date."""

    VOLATILE = 17
    """A file declared as volatile output of a step.

    This means the following:

    - Volatile files are cleaned up just like built files.
    - Volatile files cannot be used as input.
    - No hashes are computed for volatile files.
    - They can change when a step is repeated with the same inputs.
    """


REGULAR_OUTPUT_STATES = (FileState.AWAITED, FileState.BUILT, FileState.OUTDATED)
"""`FileState` values of a regular (non-volatile) output, at any point in its build lifecycle."""

TARGET_FORBIDDEN_STATES = frozenset(
    {FileState.VOLATILE, FileState.STATIC, FileState.MISSING, FileState.UNCONFIRMED}
)
"""`FileState` values a `stepup build` target file may never be in."""


class StepState(IntEnum):
    PENDING = 21
    """The step still needs to be executed."""

    RUNNING = 22
    """The step is currently executing."""

    CHECKING = 25
    """The step is being hash-checked for possible skipping.

    Named resource restrictions do not apply in this state.
    The step transitions to `SUCCEEDED` if the skip succeeds,
    or back to `PENDING` if the step must be executed.
    """

    SUCCEEDED = 23
    """The step has completed successfully (exit code 0)."""

    FAILED = 24
    """The step has failed (exit code non-zero)."""


class Need(IntEnum):
    """The degree to which a step is needed in the workflow."""

    OPTIONAL = 31
    """Only execute the step if its output is (indirectly) needed by a non-optional step."""

    DEFAULT = 32
    """Execute the step unless the user specifies targets."""

    TARGET = 33
    """Execute the step because some of its outputs are specified as targets."""

    PLAN = 34
    """Execute the step because it is part of the plan.

    Even if its outputs are not needed by any other step or among user-specified targets,
    the step is needed to fully define the workflow.
    """


class Change(IntEnum):
    UPDATED = 41
    """A file on disk has been added or changed."""

    DELETED = 42
    """A file on disk has been deleted."""

    DELETED_PARENT = 43
    """A parent directory was deleted."""


class HashUpdateCause(IntEnum):
    """The reason why file hashes are being updated in `Workflow.update_file_hashes`."""

    EXTERNAL = 51
    """File hashes changed externally (startup or watch phase)."""

    SUCCEEDED = 52
    """A step succeeded; its outputs should be marked BUILT."""

    FAILED = 53
    """A step failed or postponed; outputs remain OUTDATED or AWAITED."""

    CONFIRMED = 54
    """Client confirmed missing files exist; mark them STATIC."""
