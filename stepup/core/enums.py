# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Enumerations used in other StepUp modules.

The integer values of the enums are set by different conventions:

- ReturnCode: binary flags
- All other enums: non-overlapping values to avoid potential confusion.
"""

from enum import Flag, IntEnum, auto

__all__ = (
    "FILE_ROLE_BY_STATE",
    "FILE_STATES_BY_ROLE",
    "TARGET_FORBIDDEN_STATES",
    "Change",
    "FileRole",
    "FileState",
    "HashUpdateCause",
    "Need",
    "ReturnCode",
    "StepState",
)


class ReturnCode(Flag):
    """Composable bit flags for the return code of `stepup build`."""

    INTERNAL = auto()
    """The build could not be completed for a reason unrelated to the workflow.

    Set for a mistake in the configuration or the command line, which stops a subcommand
    before it does any work, and for an exception raised inside the director.
    """

    INTERRUPTED = auto()
    """The build was aborted by a terminal signal (Ctrl-C or `SIGTERM`).

    Set by the terminal user interface, not by the director:
    it describes how the build ended, not the state of the workflow,
    so it is combined with whatever the director reported.
    """

    FAILED = auto()
    """Some steps failed."""

    WARNING = auto()
    """The build produced a warning that does not fit the categories of the other bit flags."""

    PENDING = auto()
    """All runnable steps have completed but some non-optional steps remained pending."""

    DRAINED = auto()
    """The scheduler was draining. Pending steps are not reported."""


class FileState(IntEnum):
    """State of a file in the StepUp workflow.

    Every state but `UNDECLARED` belongs to exactly one FileRole, see `FILE_ROLE_BY_STATE`.
    `UNDECLARED` is the state of a file that has no role yet, i.e. that nothing declares.

    `CONFIRMED` and `BUILT` files are ready to be used as inputs.
    `UNDECLARED`, `UNCONFIRMED`, `MISSING`, `PLANNED`, `VOLATILE` and `OUTDATED` files are not.

    The availability and purpose of file hashes depend on the file state:

    - File hashes are available for `CONFIRMED`, `OUTDATED` and `BUILT` files.
    - They are not available for `PLANNED`, `MISSING` and `VOLATILE` files.
    - They may be present for `UNDECLARED` and `UNCONFIRMED` files,
      as a leftover from an earlier declaration,
      but are not guaranteed to be up-to-date.

    Hashes are computed for `CONFIRMED` files after they have been declared.
    Such files are first declared as `UNCONFIRMED` and then evolve to `CONFIRMED` or `MISSING`,
    depending on whether a background hash job finds the file on disk.

    The hashes of `BUILT` files are computed when the step completes.
    `OUTDATED` files keep the last hash that was computed for them.
    """

    #
    # State without a role
    #

    UNDECLARED = 11
    """A file that nothing declares, only supplied as an input to a step.

    Such a file has no role yet and its node is always detached.
    It normally evolves into a state in the `STATIC` or `OUTPUT` role,
    as soon as some plan or step declares the file.
    """

    #
    # States in the STATIC role.
    #

    UNCONFIRMED = 12
    """A file that is declared static, but whose existence (and hash) needs to be confirmed."""

    MISSING = 13
    """A file declared static, but confirmed to be absent
    (never present, or deleted by the user).
    """

    CONFIRMED = 14
    """A file that is declared static by the user and confirmed to exist.

    These are user-provided and will never be overwritten or deleted by StepUp.
    """

    #
    # States in the OUTPUT role.
    #

    PLANNED = 15
    """A file that is declared as an output of a step, but the step has not yet been executed.

    These files do not have a hash yet, and cannot be used as inputs to other steps.
    """

    BUILT = 16
    """An output of a step that has completed."""

    OUTDATED = 17
    """An old output of a step that is no longer up-to-date."""

    #
    # States in the VOLATILE role.
    #

    VOLATILE = 18
    """A file declared as a volatile output of a step.

    This means the following:

    - Volatile files are cleaned up just like built files.
    - They cannot be used as input.
    - No hashes are computed for them.
    - They can change when a step is repeated with the same inputs.
    """


class FileRole(IntEnum):
    """The role of a file in the workflow.

    This is a projection of the `FileState` enum,
    which is less sensitive to the exact phase of a file's lifecycle.
    Use it wherever the exact state within a role does not matter.
    A file that already has a role cannot migrate to another role during a single build.
    However, it may change role between builds, e.g. when the `plan.py` script is modified.

    For attached file nodes, the state determines the role,
    and the `(path, role)` claim is exclusive.
    A detached node's state is a memory of a former life, or `UNDECLARED` when it has no role yet.
    """

    STATIC = 61
    """A file that is declared static by the user."""

    OUTPUT = 62
    """A file created by a step as one of its outputs."""

    VOLATILE = 63
    """A file declared as a volatile output of a step."""


FILE_STATES_BY_ROLE = {
    FileRole.STATIC: frozenset([FileState.UNCONFIRMED, FileState.MISSING, FileState.CONFIRMED]),
    FileRole.OUTPUT: frozenset([FileState.PLANNED, FileState.BUILT, FileState.OUTDATED]),
    FileRole.VOLATILE: frozenset([FileState.VOLATILE]),
}

FILE_ROLE_BY_STATE = {
    state: role for role, states in FILE_STATES_BY_ROLE.items() for state in states
}


TARGET_FORBIDDEN_STATES = (
    FILE_STATES_BY_ROLE[FileRole.STATIC] | FILE_STATES_BY_ROLE[FileRole.VOLATILE]
)
"""`FileState` values a `stepup build` target file may never be in."""


class StepState(IntEnum):
    """The state of a step in the workflow."""

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
    """Classify file changes in the watcher."""

    UPDATED = 41
    """A file on disk has been added or changed."""

    DELETED = 42
    """A file on disk has been deleted."""

    DELETED_PARENT = 43
    """A parent directory has been deleted."""


class HashUpdateCause(IntEnum):
    """The reason why file hashes are being updated in `Workflow.update_file_hashes`."""

    EXTERNAL = 51
    """File hashes changed externally (startup or watch phase

    Steps that consume the file, or the step that creates it, become pending."""

    SUCCEEDED = 52
    """A step succeeded; its outputs are marked `BUILT`."""

    FAILED = 53
    """A step failed or deferred; its outputs end up `OUTDATED` or `PLANNED`."""

    CONFIRMED = 54
    """A declared static file was hashed; it becomes `CONFIRMED` if present, `MISSING` if not."""
