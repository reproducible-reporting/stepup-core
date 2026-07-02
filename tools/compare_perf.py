#!/usr/bin/env python3
"""Compare two director performance captures (yappi profile + SQL query log).

Usage
-----
```bash
python tmp/compare_perf.py --before-prof tmp/director-v1.prof --after-prof tmp/director-v2.prof \
    --before-sqllog tmp/sqllog-v1.json --after-sqllog tmp/sqllog-v2.json [--top N]
```

By default it compares `<dir>/director-v1.prof` / `<dir>/sqllog-v1.json` (before) against
`<dir>/director-v2.prof` / `<dir>/sqllog-v2.json` (after), where `<dir>` is this script's
directory.

See `analyze_perf.py` for the expected file formats.
"""

from __future__ import annotations

import argparse
import json
import pstats
from pathlib import Path

STEPUP_MARKER = "/stepup/core/"


def load_profile_tt(prof_path: Path) -> dict[tuple[str, str], tuple[float, float, int]]:
    """Load a pstats profile into `{(filename, funcname): (self_time, cum_time, ncalls)}`.

    The line number is deliberately dropped from the key and summed over,
    so that a function whose definition shifted line (because other code in the same
    file was edited) is still recognized as "the same function" across the two profiles,
    instead of showing up as one function disappearing and an unrelated one appearing.
    """
    stats = pstats.Stats(str(prof_path))
    merged: dict[tuple[str, str], tuple[float, float, int]] = {}
    for (filename, _lineno, funcname), (_, nc, tt, ct, _callers) in stats.stats.items():
        key = (filename, funcname)
        prev_tt, prev_ct, prev_nc = merged.get(key, (0.0, 0.0, 0))
        merged[key] = (prev_tt + tt, prev_ct + ct, prev_nc + nc)
    return merged


def compare_profiles(before_path: Path, after_path: Path, top: int) -> tuple[float, float]:
    """Print the functions whose self time changed the most between two profiles."""
    before = load_profile_tt(before_path)
    after = load_profile_tt(after_path)
    total_before = sum(tt for tt, _ct, _nc in before.values())
    total_after = sum(tt for tt, _ct, _nc in after.values())

    print(f"\n=== Profiles: {before_path.name} -> {after_path.name} ===")
    pct = 100 * (total_after - total_before) / total_before if total_before else 0.0
    print(f"Total self time: {total_before:.3f} s -> {total_after:.3f} s  ({pct:+.1f}%)")

    keys = set(before) | set(after)
    rows = []
    for key in keys:
        tt_before, _ct_before, nc_before = before.get(key, (0.0, 0.0, 0))
        tt_after, _ct_after, nc_after = after.get(key, (0.0, 0.0, 0))
        rows.append((key, tt_before, tt_after, nc_before, nc_after))
    rows.sort(key=lambda r: r[2] - r[1])

    print(f"\n-- Top {top} improved functions (self time) --")
    print(f"{'before [s]':>10} {'after [s]':>10} {'delta [s]':>10} {'ncalls (b->a)':>16}  location")
    for (filename, funcname), tt_before, tt_after, nc_before, nc_after in rows[:top]:
        delta = tt_after - tt_before
        if delta >= 0:
            continue
        ncalls_str = f"{nc_before} -> {nc_after}"
        print(
            f"{tt_before:10.3f} {tt_after:10.3f} {delta:10.3f} {ncalls_str:>16}  "
            f"{filename}({funcname})"
        )

    print(f"\n-- Top {top} regressed functions (self time) --")
    print(f"{'before [s]':>10} {'after [s]':>10} {'delta [s]':>10} {'ncalls (b->a)':>16}  location")
    for (filename, funcname), tt_before, tt_after, nc_before, nc_after in reversed(rows[-top:]):
        delta = tt_after - tt_before
        if delta <= 0:
            continue
        ncalls_str = f"{nc_before} -> {nc_after}"
        print(
            f"{tt_before:10.3f} {tt_after:10.3f} {delta:10.3f} {ncalls_str:>16}  "
            f"{filename}({funcname})"
        )

    return total_before, total_after


def compare_sqllogs(before_path: Path, after_path: Path, top: int) -> tuple[float, float]:
    """Print the queries whose total wall time changed the most between two SQL logs."""
    before = {" ".join(k.split()): v for k, v in json.loads(before_path.read_text()).items()}
    after = {" ".join(k.split()): v for k, v in json.loads(after_path.read_text()).items()}
    total_before = sum(v["wtime"] for v in before.values())
    total_after = sum(v["wtime"] for v in after.values())
    count_before = sum(v["count"] for v in before.values())
    count_after = sum(v["count"] for v in after.values())

    print(f"\n=== SQL logs: {before_path.name} -> {after_path.name} ===")
    pct = 100 * (total_after - total_before) / total_before if total_before else 0.0
    print(f"Total SQL wall time: {total_before:.3f} s -> {total_after:.3f} s  ({pct:+.1f}%)")
    print(f"Total statement executions: {count_before} -> {count_after}")
    print(f"Distinct queries: {len(before)} -> {len(after)}")

    keys = set(before) | set(after)
    rows = []
    for query in keys:
        wtime_before, count_b = (
            (before[query]["wtime"], before[query]["count"]) if query in before else (0.0, 0)
        )
        wtime_after, count_a = (
            (after[query]["wtime"], after[query]["count"]) if query in after else (0.0, 0)
        )
        rows.append((query, wtime_before, wtime_after, count_b, count_a))
    rows.sort(key=lambda r: r[2] - r[1])

    print(f"\n-- Top {top} improved queries (wall time) --")
    print(f"{'before [s]':>10} {'after [s]':>10} {'delta [s]':>10} {'count (b->a)':>14}  query")
    for query, wb, wa, cb, ca in rows[:top]:
        delta = wa - wb
        if delta >= 0:
            continue
        count_str = f"{cb} -> {ca}"
        print(f"{wb:10.3f} {wa:10.3f} {delta:10.3f} {count_str:>14}  {query[:80]}")

    print(f"\n-- Top {top} regressed queries (wall time) --")
    print(f"{'before [s]':>10} {'after [s]':>10} {'delta [s]':>10} {'count (b->a)':>14}  query")
    for query, wb, wa, cb, ca in reversed(rows[-top:]):
        delta = wa - wb
        if delta <= 0:
            continue
        count_str = f"{cb} -> {ca}"
        print(f"{wb:10.3f} {wa:10.3f} {delta:10.3f} {count_str:>14}  {query[:80]}")

    return total_before, total_after


def main() -> None:
    """Parse command-line arguments and run the before/after comparison."""
    here = Path(__file__).parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before-prof", type=Path, default=here / "director-v1.prof")
    parser.add_argument("--after-prof", type=Path, default=here / "director-v2.prof")
    parser.add_argument("--before-sqllog", type=Path, default=here / "sqllog-v1.json")
    parser.add_argument("--after-sqllog", type=Path, default=here / "sqllog-v2.json")
    parser.add_argument("--top", type=int, default=15, help="Number of rows per table.")
    args = parser.parse_args()

    total_profiled_before = total_profiled_after = None
    if args.before_prof.is_file() and args.after_prof.is_file():
        total_profiled_before, total_profiled_after = compare_profiles(
            args.before_prof, args.after_prof, args.top
        )
    else:
        print("Skipping profile comparison: one or both files are missing.")

    total_sql_before = total_sql_after = None
    if args.before_sqllog.is_file() and args.after_sqllog.is_file():
        total_sql_before, total_sql_after = compare_sqllogs(
            args.before_sqllog, args.after_sqllog, args.top
        )
    else:
        print("Skipping SQL log comparison: one or both files are missing.")

    if total_profiled_before and total_sql_before:
        pct_before = 100 * total_sql_before / total_profiled_before
        pct_after = 100 * total_sql_after / total_profiled_after
        print(
            f"\n=== SQL share of total profiled self time: "
            f"{pct_before:.1f}% -> {pct_after:.1f}% ==="
        )


if __name__ == "__main__":
    main()
