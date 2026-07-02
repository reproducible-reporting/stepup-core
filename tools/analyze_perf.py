#!/usr/bin/env python3
"""Summarize director performance from a yappi profile and a SQL query log.

Usage
-----
```bash
python tmp/analyze_perf.py [--prof tmp/director.prof] [--sqllog tmp/sqllog.json] [--top N]
```

The profile is expected in `pstats`-compatible format
(as written by `yappi.get_func_stats().save(..., type="pstat")`,
which is what `STEPUP_YAPPI` produces).
The SQL log is the JSON file written when running with `--sql-log`:
a mapping of `query text -> {"plan": ..., "wtime": ..., "count": ...}`,
where `wtime` is the summed wall time over all executions of that query
and `count` is the number of rows processed
(1 per plain `execute()`, `n` per `executemany()` with `n` rows).
"""

from __future__ import annotations

import argparse
import json
import pstats
from pathlib import Path

STEPUP_MARKER = "/stepup/core/"


def analyze_profile(prof_path: Path, top: int) -> None:
    """Print the hottest functions from a yappi/pstats profile."""
    stats = pstats.Stats(str(prof_path))
    total_tt = sum(v[2] for v in stats.stats.values())
    print(f"\n=== Profile: {prof_path} ===")
    print(f"Total self time over all functions: {total_tt:.3f} s")

    print(f"\n-- Top {top} functions by self (own) time --")
    print(f"{'self [s]':>10} {'%':>6} {'cum [s]':>10} {'ncalls':>8}  location")
    rows = sorted(stats.stats.items(), key=lambda kv: -kv[1][2])[:top]
    for (filename, lineno, funcname), (_, nc, tt, ct, _callers) in rows:
        pct = 100 * tt / total_tt if total_tt else 0.0
        print(f"{tt:10.3f} {pct:6.1f} {ct:10.3f} {nc:8d}  {filename}:{lineno}({funcname})")

    print(f"\n-- Top {top} StepUp functions by cumulative time --")
    print("(cumulative time includes time spent in callees;")
    print(" a high value with a low ncalls points at one costly call site)")
    print(f"{'cum [s]':>10} {'%':>6} {'self [s]':>10} {'ncalls':>8}  location")
    stepup_rows = [
        item for item in stats.stats.items() if STEPUP_MARKER in item[0][0].replace("\\", "/")
    ]
    stepup_rows.sort(key=lambda kv: -kv[1][3])
    for (filename, lineno, funcname), (_, nc, tt, ct, _callers) in stepup_rows[:top]:
        pct = 100 * ct / total_tt if total_tt else 0.0
        print(f"{ct:10.3f} {pct:6.1f} {tt:10.3f} {nc:8d}  {filename}:{lineno}({funcname})")


def analyze_sqllog(sqllog_path: Path, top: int) -> float:
    """Print the costliest queries from a SQL query log and return the total wall time."""
    data = json.loads(sqllog_path.read_text())
    total_wtime = sum(v["wtime"] for v in data.values())
    print(f"\n=== SQL log: {sqllog_path} ===")
    print(f"Distinct queries: {len(data)}")
    print(f"Total SQL wall time: {total_wtime:.3f} s")

    print(f"\n-- Top {top} queries by total wall time --")
    print(f"{'wtime [s]':>10} {'%':>6} {'count':>8} {'avg [ms]':>10}  query")
    rows = sorted(data.items(), key=lambda kv: -kv[1]["wtime"])[:top]
    for query, info in rows:
        wtime, count = info["wtime"], info["count"]
        pct = 100 * wtime / total_wtime if total_wtime else 0.0
        avg_ms = 1000 * wtime / count if count else 0.0
        flat_query = " ".join(query.split())
        print(f"{wtime:10.3f} {pct:6.1f} {count:8d} {avg_ms:10.4f}  {flat_query[:90]}")

    unindexed = [
        (query, info)
        for query, info in data.items()
        if "SCAN" in info["plan"] and "USING" not in info["plan"]
    ]
    if unindexed:
        print(f"\n-- Queries with a full table scan and no index ({len(unindexed)}) --")
        print(f"{'wtime [s]':>10} {'count':>8}  query")
        for query, info in sorted(unindexed, key=lambda kv: -kv[1]["wtime"]):
            flat_query = " ".join(query.split())
            print(f"{info['wtime']:10.3f} {info['count']:8d}  {flat_query[:90]}")
    else:
        print("\nNo unindexed full table scans found.")

    return total_wtime


def main() -> None:
    """Parse command-line arguments and run the profile and SQL log analyses."""
    here = Path(__file__).parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prof", type=Path, default=here / "director.prof")
    parser.add_argument("--sqllog", type=Path, default=here / "sqllog.json")
    parser.add_argument("--top", type=int, default=20, help="Number of rows per table.")
    args = parser.parse_args()

    total_profiled = None
    if args.prof.is_file():
        stats = pstats.Stats(str(args.prof))
        total_profiled = sum(v[2] for v in stats.stats.values())
        analyze_profile(args.prof, args.top)
    else:
        print(f"Profile not found: {args.prof}")

    total_sql = None
    if args.sqllog.is_file():
        total_sql = analyze_sqllog(args.sqllog, args.top)
    else:
        print(f"SQL log not found: {args.sqllog}")

    if total_profiled and total_sql:
        pct = 100 * total_sql / total_profiled
        print(f"\n=== SQL share of total profiled self time: {pct:.1f}% ===")


if __name__ == "__main__":
    main()
