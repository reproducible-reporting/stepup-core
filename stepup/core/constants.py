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
"""Shared path constants for the StepUp internal directory layout.

This module is a leaf: it imports only `path` and therefore can be imported
anywhere without risk of circular dependencies.
The constants are relative `Path` objects (a `str` subclass that does not
auto-normalize), so they can be used directly with `open`, `connect`, `glob`,
or joined against an absolute root with the `/` operator.
"""

from path import Path

__all__ = (
    "CURDIR",
    "DIRECTOR_LOG",
    "DIRECTOR_PROF",
    "FAIL_LOG",
    "GRAPH_DB",
    "PERF_DATA",
    "STEPUP_DIR",
    "SUCCESS_LOG",
    "WARNING_LOG",
)

# The current working directory, keeping the "./" prefix (distinct from Path(".")).
CURDIR = Path("./")

# The internal directory in which StepUp stores its state and logs.
STEPUP_DIR = Path(".stepup")
GRAPH_DB = STEPUP_DIR / "graph.db"
DIRECTOR_LOG = STEPUP_DIR / "director.log"
DIRECTOR_PROF = STEPUP_DIR / "director.prof"
PERF_DATA = STEPUP_DIR / "perf.data"
FAIL_LOG = STEPUP_DIR / "fail.log"
WARNING_LOG = STEPUP_DIR / "warning.log"
SUCCESS_LOG = STEPUP_DIR / "success.log"
