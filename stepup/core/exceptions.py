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


class DeferredNotConfirmedError(Exception):
    """Raised when static tree matches cannot be confirmed."""
