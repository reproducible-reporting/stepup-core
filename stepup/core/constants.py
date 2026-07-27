# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Shared path constants for the StepUp internal directory layout.

This module is a leaf: it imports only `path` and therefore can be imported
anywhere without risk of circular dependencies.
The constants are relative `Path` objects (a `str` subclass that does not
auto-normalize), so they can be used directly with `open`, `connect`, `glob`,
or joined against an absolute root with the `/` operator.
"""

from path import Path

__all__ = (
    "DIRECTOR_LOG",
    "DIRECTOR_PROF",
    "FAIL_LOG",
    "GRAPH_DB",
    "JOBLOG_CSV",
    "PERF_DATA",
    "PLAN_PY",
    "SQLLOG_CSV",
    "SQLLOG_JSON",
    "STEPUP_DIR",
    "SUCCESS_LOG",
    "WARNING_LOG",
)

# The internal directory in which StepUp stores its state and logs.
STEPUP_DIR = Path(".stepup")
GRAPH_DB = STEPUP_DIR / "graph.db"
DIRECTOR_LOG = STEPUP_DIR / "director.log"
DIRECTOR_PROF = STEPUP_DIR / "director.prof"
PERF_DATA = STEPUP_DIR / "perf.data"
FAIL_LOG = STEPUP_DIR / "fail.log"
WARNING_LOG = STEPUP_DIR / "warning.log"
SUCCESS_LOG = STEPUP_DIR / "success.log"
SQLLOG_JSON = STEPUP_DIR / "sqllog.json"
SQLLOG_CSV = STEPUP_DIR / "sqllog.csv"
JOBLOG_CSV = STEPUP_DIR / "joblog.csv"

# The boot script that StepUp executes first to define the workflow.
PLAN_PY = Path("plan.py")
