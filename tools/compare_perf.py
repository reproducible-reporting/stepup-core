#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Compare two director performance captures (yappi profile + SQL query log).

Usage
-----
```bash
python tools/compare_perf.py before_suffix after_suffix [--top N]
```

`before_suffix` and `after_suffix` are appended to the default file names,
e.g. with `_v1` and `_v2`, it compares `director_v1.prof` / `sqllog_v1.json` (before)
against `director_v2.prof` / `sqllog_v2.json` (after), all in the current directory.

See `analyze_perf.py` for the expected file formats.
"""

from __future__ import annotations

import argparse
import json
import pstats
from pathlib import Path

from common import (
    add_top_argument,
    flatten_query,
    pct,
    prof_path,
    sqllog_path,
    strip_site_packages,
)


def load_profile_tt(prof_file: Path) -> dict[tuple[str, str], tuple[float, float, int]]:
    """Load a pstats profile into `{(filename, funcname): (self_time, cum_time, ncalls)}`.

    The line number is deliberately dropped from the key and summed over,
    so that a function whose definition shifted line (because other code in the same
    file was edited) is still recognized as "the same function" across the two profiles,
    instead of showing up as one function disappearing and an unrelated one appearing.
    """
    stats = pstats.Stats(str(prof_file))
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
    delta_pct = pct(total_after - total_before, total_before)
    print(f"Total self time: {total_before:.3f} s -> {total_after:.3f} s  ({delta_pct:+.1f}%)")

    keys = set(before) | set(after)
    rows = []
    for key in keys:
        tt_before, _ct_before, nc_before = before.get(key, (0.0, 0.0, 0))
        tt_after, _ct_after, nc_after = after.get(key, (0.0, 0.0, 0))
        rows.append((key, tt_before, tt_after, nc_before, nc_after))
    rows.sort(key=lambda r: r[2] - r[1])

    print(f"\n-- Top {top} improved functions (self time) --")
    print(f"{'before [s]':>10} {'after [s]':>10} {'delta [s]':>10} {'ncalls (b->a)':>20}  location")
    for (filename, funcname), tt_before, tt_after, nc_before, nc_after in rows[:top]:
        delta = tt_after - tt_before
        if delta >= 0:
            continue
        ncalls_str = f"{nc_before} -> {nc_after}"
        location = strip_site_packages(filename)
        print(
            f"{tt_before:10.3f} {tt_after:10.3f} {delta:10.3f} {ncalls_str:>20}  "
            f"{location}({funcname})"
        )

    print(f"\n-- Top {top} regressed functions (self time) --")
    print(f"{'before [s]':>10} {'after [s]':>10} {'delta [s]':>10} {'ncalls (b->a)':>20}  location")
    for (filename, funcname), tt_before, tt_after, nc_before, nc_after in reversed(rows[-top:]):
        delta = tt_after - tt_before
        if delta <= 0:
            continue
        ncalls_str = f"{nc_before} -> {nc_after}"
        location = strip_site_packages(filename)
        print(
            f"{tt_before:10.3f} {tt_after:10.3f} {delta:10.3f} {ncalls_str:>20}  "
            f"{location}({funcname})"
        )

    return total_before, total_after


def load_sqllog(path: Path) -> dict[tuple[str, str], dict]:
    """Load a SQL query log into `{(module_name, flattened query): record}`.

    The line number is deliberately dropped from the key and summed over,
    so that a call site whose line shifted (e.g. because an optimization
    added or removed code above it) is still recognized as "the same query"
    across the two logs, instead of showing up as one entry disappearing
    and an unrelated one appearing.
    """
    records = json.loads(path.read_text())
    merged: dict[tuple[str, str], dict] = {}
    for rec in records:
        key = (rec["module_name"], flatten_query(rec["query"]))
        prev_wtime, prev_count = (
            (merged[key]["wtime"], merged[key]["count"]) if key in merged else (0.0, 0)
        )
        merged[key] = {"wtime": prev_wtime + rec["wtime"], "count": prev_count + rec["count"]}
    return merged


def compare_sqllogs(before_path: Path, after_path: Path, top: int) -> tuple[float, float]:
    """Print the queries whose total wall time changed the most between two SQL logs."""
    before = load_sqllog(before_path)
    after = load_sqllog(after_path)
    total_before = sum(rec["wtime"] for rec in before.values())
    total_after = sum(rec["wtime"] for rec in after.values())
    count_before = sum(rec["count"] for rec in before.values())
    count_after = sum(rec["count"] for rec in after.values())

    print(f"\n=== SQL logs: {before_path.name} -> {after_path.name} ===")
    delta_pct = pct(total_after - total_before, total_before)
    print(f"Total SQL wall time: {total_before:.3f} s -> {total_after:.3f} s  ({delta_pct:+.1f}%)")
    print(f"Total statement executions: {count_before} -> {count_after}")
    print(f"Distinct module/query pairs: {len(before)} -> {len(after)}")

    keys = set(before) | set(after)
    rows = []
    for key in keys:
        wtime_before, count_b = (
            (before[key]["wtime"], before[key]["count"]) if key in before else (0.0, 0)
        )
        wtime_after, count_a = (
            (after[key]["wtime"], after[key]["count"]) if key in after else (0.0, 0)
        )
        rows.append((key, wtime_before, wtime_after, count_b, count_a))
    rows.sort(key=lambda r: r[2] - r[1])

    print(f"\n-- Top {top} improved queries (wall time) --")
    print(
        f"{'before [s]':>10} {'after [s]':>10} {'delta [s]':>10} {'count (b->a)':>18}  "
        f"{'location':>24}  query"
    )
    for (module_name, query), wb, wa, cb, ca in rows[:top]:
        delta = wa - wb
        if delta >= 0:
            continue
        count_str = f"{cb} -> {ca}"
        print(
            f"{wb:10.3f} {wa:10.3f} {delta:10.3f} {count_str:>18}  {module_name:>24}  {query[:60]}"
        )

    print(f"\n-- Top {top} regressed queries (wall time) --")
    print(
        f"{'before [s]':>10} {'after [s]':>10} {'delta [s]':>10} {'count (b->a)':>18}  "
        f"{'location':>24}  query"
    )
    for (module_name, query), wb, wa, cb, ca in reversed(rows[-top:]):
        delta = wa - wb
        if delta <= 0:
            continue
        count_str = f"{cb} -> {ca}"
        print(
            f"{wb:10.3f} {wa:10.3f} {delta:10.3f} {count_str:>18}  {module_name:>24}  {query[:60]}"
        )

    return total_before, total_after


def main() -> None:
    """Parse command-line arguments and run the before/after comparison."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("before_suffix", help="Suffix of the 'before' file names.")
    parser.add_argument("after_suffix", help="Suffix of the 'after' file names.")
    add_top_argument(parser, 15)
    args = parser.parse_args()

    before_prof = prof_path(args.before_suffix)
    after_prof = prof_path(args.after_suffix)
    before_sqllog = sqllog_path(args.before_suffix)
    after_sqllog = sqllog_path(args.after_suffix)

    total_profiled_before = total_profiled_after = None
    if before_prof.is_file() and after_prof.is_file():
        total_profiled_before, total_profiled_after = compare_profiles(
            before_prof, after_prof, args.top
        )
    else:
        print("Skipping profile comparison: one or both files are missing.")

    total_sql_before = total_sql_after = None
    if before_sqllog.is_file() and after_sqllog.is_file():
        total_sql_before, total_sql_after = compare_sqllogs(before_sqllog, after_sqllog, args.top)
    else:
        print("Skipping SQL log comparison: one or both files are missing.")

    if total_profiled_before and total_sql_before:
        pct_before = pct(total_sql_before, total_profiled_before)
        pct_after = pct(total_sql_after, total_profiled_after)
        print(
            f"\n=== SQL share of total profiled self time: "
            f"{pct_before:.1f}% -> {pct_after:.1f}% ==="
        )


if __name__ == "__main__":
    main()
