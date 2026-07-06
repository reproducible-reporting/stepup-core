#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Summarize director performance from a yappi profile and a SQL query log.

Usage
-----
```bash
python tools/analyze_perf.py [suffix] [--top N] [--min-pct PCT]
```

`suffix` is appended to the default file names, e.g. with suffix `_v1`,
the files read are `director_v1.prof` and `sqllog_v1.json` in the current directory.

The profile is expected in `pstats`-compatible format
(as written by `yappi.get_func_stats().save(..., type="pstat")`,
which is what `STEPUP_YAPPI` produces).
The SQL log is the JSON file written when running with `--sqllog`:
a list of records, one per distinct call site,
each `{"query": ..., "module_name": ..., "line": ..., "plan": ..., "wtime": ..., "count": ...}`,
where `module_name` / `line` identify the `db.execute()` / `db.executemany()` call site,
`wtime` is the summed wall time over all executions of that query at that call site,
and `count` is the number of rows processed
(1 per plain `execute()`, `n` per `executemany()` with `n` rows).
"""

from __future__ import annotations

import argparse
import json
import pstats
from pathlib import Path

from common import (
    PLAN_ISSUE_LABELS,
    STEPUP_MARKER,
    add_top_argument,
    attribute_to_stepup,
    classify_plan_lines,
    flatten_query,
    pct,
    prof_path,
    ranked_rows,
    section_limit,
    sql_location,
    sqllog_path,
    strip_site_packages,
)


def analyze_profile(prof_file: Path, top: int, min_pct: float | None) -> None:
    """Print the hottest functions from a yappi/pstats profile."""
    stats = pstats.Stats(str(prof_file))
    stats_dict = stats.stats
    total_tt = sum(v[2] for v in stats_dict.values())
    print(f"\n=== Profile: {prof_file} ===")
    print(f"Total self time over all functions: {total_tt:.3f} s")

    print(f"\n-- Top {section_limit(top, min_pct)} functions by self (own) time --")
    print(
        f"{'self [s]':>10} {'%':>6} {'avg [ms]':>9} {'cov %':>6} {'cum [s]':>10} {'ncalls':>8}  "
        "location"
    )
    rows = sorted(stats_dict.items(), key=lambda kv: -kv[1][2])
    for (key, (_, nc, tt, ct, _callers)), running_pct in ranked_rows(
        rows, top, min_pct, lambda item: item[1][2], total_tt
    ):
        filename, lineno, funcname = key
        avg_ms = 1000 * tt / nc if nc else 0.0
        location = strip_site_packages(filename)
        print(
            f"{tt:10.3f} {pct(tt, total_tt):6.1f} {avg_ms:9.3f} {running_pct:6.1f} "
            f"{ct:10.3f} {nc:8d}  {location}:{lineno}({funcname})"
        )
        if STEPUP_MARKER not in filename.replace("\\", "/"):
            attributed = attribute_to_stepup(stats_dict, key)
            if attributed is not None and attributed != key:
                a_filename, a_lineno, a_funcname = attributed
                a_location = strip_site_packages(a_filename)
                print(f"{'':>44}via {a_location}:{a_lineno}({a_funcname})")

    print(f"\n-- Top {section_limit(top, min_pct)} StepUp functions by cumulative time --")
    print("(cumulative time includes time spent in callees;")
    print(" a high value with a low ncalls points at one costly call site)")
    print("(cov % can exceed 100% here: nested calls count toward multiple ancestors)")
    print(
        f"{'cum [s]':>10} {'%':>6} {'avg [ms]':>9} {'cov %':>6} {'self [s]':>10} {'ncalls':>8}  "
        "location"
    )
    stepup_rows = [
        item for item in stats_dict.items() if STEPUP_MARKER in item[0][0].replace("\\", "/")
    ]
    stepup_rows.sort(key=lambda kv: -kv[1][3])
    for (key, (_, nc, tt, ct, _callers)), running_pct in ranked_rows(
        stepup_rows, top, min_pct, lambda item: item[1][3], total_tt
    ):
        filename, lineno, funcname = key
        avg_ms = 1000 * ct / nc if nc else 0.0
        location = strip_site_packages(filename)
        print(
            f"{ct:10.3f} {pct(ct, total_tt):6.1f} {avg_ms:9.3f} {running_pct:6.1f} "
            f"{tt:10.3f} {nc:8d}  {location}:{lineno}({funcname})"
        )


def analyze_sqllog(sqllog_file: Path, top: int, min_pct: float | None) -> float:
    """Print the costliest queries from a SQL query log and return the total wall time."""
    data = json.loads(sqllog_file.read_text())
    total_wtime = sum(rec["wtime"] for rec in data)
    distinct_queries = len({rec["query"] for rec in data})
    print(f"\n=== SQL log: {sqllog_file} ===")
    print(f"Distinct call sites: {len(data)}")
    print(f"Distinct queries: {distinct_queries}")
    print(f"Total SQL wall time: {total_wtime:.3f} s")

    print(f"\n-- Top {section_limit(top, min_pct)} queries by total wall time --")
    print(
        f"{'wtime [s]':>10} {'%':>6} {'cov %':>6} {'count':>8} {'avg [ms]':>10}"
        "                        location  query"
    )
    rows = sorted(data, key=lambda rec: -rec["wtime"])
    for rec, running_pct in ranked_rows(rows, top, min_pct, lambda r: r["wtime"], total_wtime):
        wtime, count = rec["wtime"], rec["count"]
        avg_ms = 1000 * wtime / count if count else 0.0
        flat_query = flatten_query(rec["query"])
        location = sql_location(rec["module_name"], rec["line"])
        print(
            f"{wtime:10.3f} {pct(wtime, total_wtime):6.1f} {running_pct:6.1f} {count:8d} "
            f"{avg_ms:10.4f}  {location:>30}  {flat_query[:70]}"
        )

    plan_issues = [(rec, classify_plan_lines(rec["plan"])) for rec in data]
    for key, label in PLAN_ISSUE_LABELS.items():
        flagged = [rec for rec, issues in plan_issues if key in issues]
        if flagged:
            print(f"\n-- Queries with {label} ({len(flagged)}) --")
            print(f"{'wtime [s]':>10} {'count':>8}                        location  query")
            for rec in sorted(flagged, key=lambda rec: -rec["wtime"]):
                flat_query = flatten_query(rec["query"])
                location = sql_location(rec["module_name"], rec["line"])
                print(f"{rec['wtime']:10.3f} {rec['count']:8d}  {location:>30}  {flat_query[:70]}")
        else:
            print(f"\nNo queries with {label} found.")

    return total_wtime


def main() -> None:
    """Parse command-line arguments and run the profile and SQL log analyses."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("suffix", nargs="?", default="", help="Suffix appended to file names.")
    add_top_argument(parser, 20)
    args = parser.parse_args()

    prof_file = prof_path(args.suffix)
    sqllog_file = sqllog_path(args.suffix)

    total_profiled = None
    if prof_file.is_file():
        stats = pstats.Stats(str(prof_file))
        total_profiled = sum(v[2] for v in stats.stats.values())
        analyze_profile(prof_file, args.top, args.min_pct)
    else:
        print(f"Profile not found: {prof_file}")

    total_sql = None
    if sqllog_file.is_file():
        total_sql = analyze_sqllog(sqllog_file, args.top, args.min_pct)
    else:
        print(f"SQL log not found: {sqllog_file}")

    if total_profiled and total_sql:
        sql_pct = pct(total_sql, total_profiled)
        print(f"\n=== SQL share of total profiled self time: {sql_pct:.1f}% ===")


if __name__ == "__main__":
    main()
