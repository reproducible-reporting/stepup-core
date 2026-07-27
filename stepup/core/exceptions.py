# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Exceptions used in StepUp."""


class GraphError(Exception):
    """A change to the graph could not be made as it would introduce an inconsistency."""


class CyclicError(GraphError):
    """Adding a new relation would introduce a cyclic dependency."""


class RPCError(Exception):
    """A remote procedure call could not be interpreted correctly."""


class StepUpError(ValueError):
    """Invalid argument passed to a StepUp user- or extension-facing API function."""


class PathError(StepUpError):
    """A path argument is invalid.

    Raised when a path does not exist, has the wrong type
    (e.g. a directory where a file is required),
    or violates the leading `./` / trailing `/` affix contract.
    """


class EnvVarError(StepUpError):
    """An environment variable referenced in a path or string could not be resolved."""


class InputNotFoundError(Exception):
    """Raised when amended inputs are not available yet."""


class CgroupError(RuntimeError):
    """Cgroup v2 accounting is unavailable or unusable for this process."""


class TUIError(RuntimeError):
    """An error raised by the terminal user interface (`tui.py`) before the director starts.

    Distinguished from a plain `RuntimeError` so that the top-level handler in
    `build_tool` (`tui.py`) can catch it and print a short, user-facing message
    (`ERROR: ...` on stderr) instead of a full traceback, then exit with
    `ReturnCode.INTERNAL`.
    """


class RunError(RuntimeError):
    """An error raised by the `run` module while executing a command."""


class HashCancelledError(Exception):
    """Raised from `FileHash.regen` when `cancel_event` was set."""
