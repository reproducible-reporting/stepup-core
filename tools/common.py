# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Shared utilities for `analyze_perf.py` and `compare_perf.py`."""

from __future__ import annotations

import argparse
import re
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import TypeVar

STEPUP_MARKER = "/stepup/core/"
SITE_PACKAGES_RE = re.compile(r".*[/\\]python\d+\.\d+[/\\]site-packages[/\\]")

# A pstats function key: (filename, lineno, funcname).
StatKey = tuple[str, int, str]
# A pstats function record: (call_count, ncalls, total_time, cumulative_time, callers).
StatValue = tuple[int, int, float, float, dict]

_T = TypeVar("_T")


def strip_site_packages(filename: str) -> str:
    """Strip a long venv prefix up to and including `pythonX.Y/site-packages/`."""
    return SITE_PACKAGES_RE.sub("", filename, count=1)


def pct(part: float, whole: float) -> float:
    """Compute the percentage of `part` relative to `whole`, or 0.0 if `whole` is zero."""
    return 100 * part / whole if whole else 0.0


def flatten_query(query: str) -> str:
    """Collapse a SQL query's whitespace into single spaces."""
    return " ".join(query.split())


def sql_location(module_name: str, line: int) -> str:
    """Format a SQL call site as `module_name:line`."""
    return f"{module_name}:{line}"


PLAN_ISSUE_LABELS: dict[str, str] = {
    "full_scan": "a full table scan without any index",
    "automatic_index": "an automatic index built at query time",
    "temp_btree": "a temporary B-tree for ORDER BY / GROUP BY / DISTINCT",
    "correlated_subquery": "a correlated subquery (CO-ROUTINE / SCALAR SUBQUERY)",
}


def classify_plan_lines(plan: str) -> set[str]:
    """Classify a query plan's lines into performance-risk categories.

    `plan` is checked line by line (rather than as one substring search),
    so that e.g. a join where only one of several tables is fully scanned
    is still flagged, even though another line in the same plan
    mentions `USING INDEX`.

    Returns
    -------
    issues
        The subset of `PLAN_ISSUE_LABELS` keys with at least one matching line.
    """
    issues: set[str] = set()
    for raw_line in plan.splitlines():
        line = raw_line.strip()
        if "AUTOMATIC" in line and "INDEX" in line:
            issues.add("automatic_index")
        elif line.startswith("SCAN") and "USING INDEX" not in line:
            issues.add("full_scan")
        if line.startswith("USE TEMP B-TREE"):
            issues.add("temp_btree")
        if line.startswith("CO-ROUTINE") or "SCALAR SUBQUERY" in line:
            # Catches both the bare "SCALAR SUBQUERY N" (CO-ROUTINE-style) label and
            # SQLite's "CORRELATED SCALAR SUBQUERY N" prefix used for correlated subqueries.
            issues.add("correlated_subquery")
    return issues


def prof_path(suffix: str) -> Path:
    """The path of the yappi profile file for a given suffix."""
    return Path(f"director{suffix}.prof")


def sqllog_path(suffix: str) -> Path:
    """The path of the SQL query log index file for a given suffix."""
    return Path(f"sqllog{suffix}.json")


def sqlcsv_path(suffix: str) -> Path:
    """The path of the `--sqllog` per-execution timing CSV file for a given suffix."""
    return Path(f"sqllog{suffix}.csv")


def perf_data_path(suffix: str) -> Path:
    """The path of the `perf record` output file for a given suffix."""
    return Path(f"perf{suffix}.data")


def joblog_path(suffix: str) -> Path:
    """The path of the `--joblog` CSV file for a given suffix."""
    return Path(f"joblog{suffix}.csv")


def add_top_argument(parser: argparse.ArgumentParser, default: int) -> None:
    """Add the shared `--top` / `--min-pct` arguments to an argument parser."""
    parser.add_argument(
        "--top", type=int, default=default, help="Maximum number of rows per table."
    )
    parser.add_argument(
        "--min-pct",
        type=float,
        default=None,
        help="Show enough rows to cover at least this percentage of the table's total metric, "
        "still capped by --top.",
    )


def section_limit(top: int, min_pct: float | None) -> str:
    """Format the row-count description for a ranked-table section heading."""
    if min_pct is None:
        return str(top)
    return f"{top} (stopping early once {min_pct:g}% is covered)"


def ranked_rows(
    rows: Iterable[_T],
    top: int,
    min_pct: float | None,
    value_of: Callable[[_T], float],
    total: float,
) -> Iterator[tuple[_T, float]]:
    """Yield `(row, running_pct)` pairs from a metric-sorted `rows` sequence.

    Stops once `top` rows have been yielded, or once the running percentage
    of `total` reaches `min_pct` (if given), whichever comes first.
    """
    running = 0.0
    for i, row in enumerate(rows):
        if i >= top:
            return
        running += value_of(row)
        running_pct = pct(running, total)
        yield row, running_pct
        if min_pct is not None and running_pct >= min_pct:
            return


def attribute_to_stepup(
    stats: dict[StatKey, StatValue], key: StatKey, max_depth: int = 10
) -> StatKey | None:
    """Walk up the highest-time caller chain to find the nearest StepUp frame.

    Used to attribute the cost of a generic library or builtin function
    (which is meaningless on its own) back to the StepUp code that calls it.
    Returns `None` if no StepUp frame is found within `max_depth` hops.
    """
    visited = set()
    current = key
    for _ in range(max_depth):
        if current in visited:
            return None
        visited.add(current)
        if STEPUP_MARKER in current[0].replace("\\", "/"):
            return current
        callers = stats.get(current, (0, 0, 0.0, 0.0, {}))[4]
        if not callers:
            return None
        current = max(callers.items(), key=lambda kv: kv[1][2])[0]
    return None
