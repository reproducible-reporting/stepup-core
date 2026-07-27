#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Summarize director performance from a yappi profile, a SQL query log, and a perf.data file.

Usage
-----
```bash
python tools/analyze_perf.py [suffix] [--top N] [--min-pct PCT]
```

`suffix` is appended to the default file names, e.g. with suffix `_v1`,
the files read are `director_v1.prof`, `sqllog_v1.json`, and `perf_v1.data`
in the current directory.

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
The perf.data file is the raw output of a Linux `perf record` capture
(as produced by `stepup build --perf`, which sets `STEPUP_BUILD_PERF`).
It is analyzed in place via `perf report --stdio -g none`,
so no manual `perf script` conversion step is needed.
"""

from __future__ import annotations

import argparse
import json
import pstats
import re
import shutil
import subprocess
from pathlib import Path

from common import (
    PLAN_ISSUE_LABELS,
    STEPUP_MARKER,
    add_top_argument,
    attribute_to_stepup,
    classify_plan_lines,
    flatten_query,
    pct,
    perf_data_path,
    prof_path,
    ranked_rows,
    section_limit,
    sql_location,
    sqllog_path,
    strip_site_packages,
)

# A perf report row key: (shared object, symbol).
PerfKey = tuple[str, str]
# A perf report row's accumulated (children, self) event counts, summed across
# hybrid-CPU event-group sections (e.g. cpu_atom/cycles and cpu_core/cycles).
PerfCounts = tuple[float, float]

PERF_SECTION_RE = re.compile(r"^# Samples: .* of event '(?P<event>[^']+)'\s*$")
PERF_EVENT_COUNT_RE = re.compile(r"^# Event count \(approx\.\): (?P<count>\d+)\s*$")
PERF_ROW_RE = re.compile(
    r"^\s*(?P<children>[\d.]+)%\s+(?P<self>[\d.]+)%\s+(?P<command>\S+)\s+"
    r"(?P<dso>.+?)\s+\[\.\]\s+(?P<symbol>.+?)\s*$"
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


def parse_perf_report(text: str) -> tuple[dict[PerfKey, PerfCounts], dict[str, float]]:
    """Parse `perf report --stdio -g none` output into merged per-symbol counts.

    On hybrid CPUs (e.g. Intel P-core/E-core), `perf` reports samples in separate
    sections per hardware event group (`cpu_atom/cycles`, `cpu_core/cycles`, ...),
    each with its own `Children` / `Self` percentages relative to that section's
    `Event count (approx.)`. This merges all sections into one ranking by converting
    each row's percentages back into weighted counts before summing across sections.

    Returns
    -------
    counts
        `{(dso, symbol): (children_count, self_count)}`, summed over all sections.
    events
        `{event_name: event_count}`, one entry per event-group section found.
    """
    counts: dict[PerfKey, PerfCounts] = {}
    events: dict[str, float] = {}
    event_name: str | None = None
    event_count = 0.0
    for line in text.splitlines():
        section_match = PERF_SECTION_RE.match(line)
        if section_match:
            event_name = section_match.group("event")
            event_count = 0.0
            continue
        count_match = PERF_EVENT_COUNT_RE.match(line)
        if count_match:
            event_count = float(count_match.group("count"))
            if event_name is not None:
                events[event_name] = event_count
            continue
        row_match = PERF_ROW_RE.match(line)
        if not row_match or not event_count:
            continue
        key = (row_match.group("dso"), row_match.group("symbol"))
        children = float(row_match.group("children")) / 100 * event_count
        self_ = float(row_match.group("self")) / 100 * event_count
        prev_children, prev_self = counts.get(key, (0.0, 0.0))
        counts[key] = (prev_children + children, prev_self + self_)
    return counts, events


# Prefix `perf`'s CPython trampoline puts on every Python-frame symbol.
PY_SYMBOL_PREFIX = "py::"


def print_cumulative_table(
    counts: dict[PerfKey, PerfCounts],
    total_self: float,
    top: int,
    min_pct: float | None,
    title: str,
    symbol_filter: str,
) -> None:
    """Print a ranked table of symbols matching `symbol_filter`, by children (cumulative) time.

    Self time is not used for ranking here:
    `perf`'s CPython trampoline only marks call boundaries,
    so a Python frame's self time is always close to zero
    (its own bytecode work is attributed to the interpreter's C-level eval loop instead).
    Children time is therefore the only metric that meaningfully ranks Python frames.
    """
    print(f"\n-- Top {section_limit(top, min_pct)} {title} --")
    print(f"{'children %':>10} {'cov %':>6} {'self %':>8}  symbol")
    rows = [item for item in counts.items() if symbol_filter in item[0][1]]
    rows.sort(key=lambda kv: -kv[1][0])
    for (key, (children, self_)), running_pct in ranked_rows(
        rows, top, min_pct, lambda item: item[1][0], total_self
    ):
        _dso, symbol = key
        print(
            f"{pct(children, total_self):10.2f} {running_pct:6.1f} "
            f"{pct(self_, total_self):8.2f}  {symbol}"
        )


def analyze_perf_data(perf_data_file: Path, top: int, min_pct: float | None) -> None:
    """Print the hottest Python functions from a Linux `perf record` output file.

    The file is analyzed with `perf report --stdio -g none`,
    which produces one row per unique `(shared object, symbol)` pair,
    together with a `Children` percentage (self + descendants) and a `Self` percentage.
    See `parse_perf_report` for how hybrid-CPU event-group sections are merged.

    Only `py::`-prefixed symbols (Python frames) are reported,
    since native library and interpreter internals (e.g. SQLite, libpython)
    are not directly actionable from StepUp's own Python code.
    Percentages are relative to total self time across all sections,
    and may not sum to 100% because `perf` cannot resolve every sample.
    """
    print(f"\n=== perf.data: {perf_data_file} ===")
    if shutil.which("perf") is None:
        print("Skipping: the `perf` command is not available on this system.")
        return

    result = subprocess.run(
        ["perf", "report", "--stdio", "-g", "none", "-i", str(perf_data_file)],
        capture_output=True,
        text=True,
        check=False,
    )
    counts, events = parse_perf_report(result.stdout)
    if not counts:
        print("No samples could be parsed from the `perf report` output.")
        if result.stderr:
            print(result.stderr.strip())
        return

    for event, count in events.items():
        print(f"Event group {event!r}: approx. {count:,.0f} events")
    total_self = sum(self_ for _children, self_ in counts.values())

    print_cumulative_table(
        counts, total_self, top, min_pct, "Python functions by cumulative time", PY_SYMBOL_PREFIX
    )
    print_cumulative_table(
        counts, total_self, top, min_pct, "StepUp functions by cumulative time", STEPUP_MARKER
    )


def main() -> None:
    """Parse command-line arguments and run the profile, SQL log, and perf.data analyses."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("suffix", nargs="?", default="", help="Suffix appended to file names.")
    add_top_argument(parser, 20)
    args = parser.parse_args()

    prof_file = prof_path(args.suffix)
    sqllog_file = sqllog_path(args.suffix)
    perf_data_file = perf_data_path(args.suffix)

    total_profiled = None
    if prof_file.is_file():
        stats = pstats.Stats(str(prof_file))
        total_profiled = sum(v[2] for v in stats.stats.values())
        analyze_profile(prof_file, args.top, args.min_pct)
    else:
        print(f"Profile not found: {prof_file}")

    if perf_data_file.is_file():
        analyze_perf_data(perf_data_file, args.top, args.min_pct)
    else:
        print(f"perf.data not found: {perf_data_file}")

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
