# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Exceptions used in StepUp."""

__all__ = (
    "AmendWhileHoldingError",
    "CgroupError",
    "ConfigError",
    "ConsistencyError",
    "CyclicError",
    "EnvVarError",
    "GraphError",
    "HashCancelledError",
    "HashError",
    "HashFailedError",
    "InputNotFoundError",
    "PathError",
    "RPCError",
    "RunError",
    "StepUpError",
    "ToolError",
    "UsageError",
)


#
# Usage Errors
#


class UsageError(Exception):
    """Base class for errors that the user can fix by changing their own code.

    These reach the client as a short message without a director-side traceback,
    unless `STEPUP_DEBUG` is set.
    Any other exception keeps the full traceback,
    because it indicates a bug in StepUp rather than in the user's plan.
    """


class ConfigError(UsageError, ValueError):
    """A configuration file or environment variable holds something StepUp cannot use.

    `ValueError` is kept in the bases because most of the underlying problems
    (bad TOML syntax, a value of the wrong type, a value outside the allowed choices)
    surface as a `ValueError` before being wrapped in this class.

    The command-line interface reports these without a traceback,
    because the raise site says nothing a user could act on:
    the message names the config file or environment variable to fix.
    """


class ToolError(UsageError):
    """An error raised by a command line tool that must be shown without traceback."""


#
# Graph Errors (special case of Usage Error)
#


class GraphError(UsageError):
    """A change to the graph could not be made as it would introduce an inconsistency."""


class AmendWhileHoldingError(GraphError):
    """`amend(inp=...)` was called while the calling step has an open `hold()` block."""


class CyclicError(GraphError):
    """Adding a new relation would introduce a cyclic dependency."""


#
# StepUp API usage errors
#


class StepUpError(UsageError, ValueError):
    """Invalid argument passed to a StepUp user- or extension-facing API function.

    `ValueError` is kept in the bases so that existing `except ValueError` code
    keeps catching these errors.
    """


class PathError(StepUpError):
    """A path argument is invalid.

    Raised when a path does not exist, has the wrong type
    (e.g. a directory where a file is required),
    or violates the leading `./` / trailing `/` affix contract.
    """


class EnvVarError(StepUpError):
    """An environment variable referenced in a path or string could not be resolved."""


#
# Internal Errors
#


class ConsistencyError(RuntimeError):
    """An invariant of the workflow graph is violated.

    Deliberately not a `UsageError`:
    no plan can put the graph in such a state through the public API,
    so this always points at a bug in StepUp
    (or at a database corrupted by something outside it),
    and the full traceback is what a bug report needs.
    """


class RPCError(Exception):
    """A remote procedure call could not be interpreted correctly."""


class InputNotFoundError(Exception):
    """Raised when dynamic inputs are not available yet."""


class CgroupError(RuntimeError):
    """Cgroup v2 accounting is unavailable or unusable for this process."""


class RunError(RuntimeError):
    """An error raised by the `run` module while executing a command."""


class HashError(Exception):
    """Base class for errors raised while computing a file hash."""


class HashCancelledError(HashError):
    """Raised when a hash computation was aborted because its `cancel_event` was set."""


class HashFailedError(HashError):
    """Raised when a file cannot be hashed."""
