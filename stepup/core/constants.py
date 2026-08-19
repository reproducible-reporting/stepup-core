# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Shared constants across StepUp."""

from path import Path

__all__ = (
    "CORE_ENV_VARS",
    "DIRECTOR_LOG",
    "DIRECTOR_LOG_DESCRIPTION",
    "DIRECTOR_LOG_PID_PREFIX",
    "DIRECTOR_LOG_SOCKET_PREFIX",
    "DIRECTOR_PROF",
    "DIRECTOR_SOCKET_SENTINEL",
    "FAIL_LOG",
    "GRAPH_DB",
    "INTERNAL_ENV_VARS",
    "JOBLOG_CSV",
    "PERF_DATA",
    "PLAN_PY",
    "RENDER_JINJA_MODES",
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
# How an RPC client refers to the director's log when it must send the user there.
DIRECTOR_LOG_DESCRIPTION = f"`{DIRECTOR_LOG}`"
# The first two lines of `DIRECTOR_LOG` announce where the director listens and which
# process it is, so that a client can find a running director without a handshake.
# Both ends of this format live here, since the writer and the reader are different modules.
DIRECTOR_LOG_SOCKET_PREFIX = "SOCKET "
DIRECTOR_LOG_PID_PREFIX = "PID "
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

# The value that the director assigns to its own STEPUP_DIRECTOR_SOCKET environment variable.
# The director must never call itself over RPC,
# so it makes the variable name a socket that cannot be connected to
# instead of leaving it unset, which would silently yield a dummy client.
DIRECTOR_SOCKET_SENTINEL = "_invalid_socket_for_director_process_"

# The delimiter styles supported by the `render-jinja` console script,
# in the order in which they are shown to the user.
RENDER_JINJA_MODES = ("auto", "plain", "latex")

CORE_ENV_VARS = frozenset(
    {
        "STEPUP_DEBUG",
        "STEPUP_MAX_OUTPUT_SIZE",
        "STEPUP_PATH_FILTER",
        "STEPUP_ROOT",
        "STEPUP_SYNC_RPC_TIMEOUT",
    }
)
"""The variables StepUp Core acts on without a subcommand defining a setting for them.

Maintained by hand, together with `INTERNAL_ENV_VARS`:
nothing derives either set from the places that read these variables,
which `test_env_vars_are_classified` guards against.
A variable with the prefix that is in neither set nor in
`ConfigLoader.recognized_env_vars` does nothing,
which is how a typo in a variable name becomes visible.

Extension packages are not covered:
their settings are recognized through their patched parsers,
but the variables they use internally are not listed here.
"""

INTERNAL_ENV_VARS = frozenset(
    {
        "STEPUP_DIRECTOR_SOCKET",
        "STEPUP_JOB_I",
        "STEPUP_REPORTER_SOCKET",
        "STEPUP_STEP_INP_DIGEST",
        "STEPUP_STEP_NEED",
    }
)
"""The variables that StepUp sets itself for the subprocesses it starts.

Whatever the environment holds for one of these is overwritten before a step sees it,
so setting one by hand configures nothing and is most likely a mistake.
`stepup config` therefore lists them in a group of their own,
apart from the variables that nothing reads at all.
"""
