# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Shared path constants across StepUp."""

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

# The planning script that StepUp executes first to define the workflow.
PLAN_PY = Path("plan.py")
