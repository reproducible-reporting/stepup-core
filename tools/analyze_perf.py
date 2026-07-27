#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Summarize director performance from a yappi profile, a SQL query log, a perf.data file,
and a `--joblog` CSV file.

Usage
-----
```bash
python tools/analyze_perf.py [suffix] [--top N] [--min-pct PCT]
```

`suffix` is appended to the default file names, e.g. with suffix `_v1`,
the files read are `director_v1.prof`, `sqllog_v1.json`, `perf_v1.data`,
and `joblog_v1.csv` in the current directory.

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
The job log is the CSV file written when running with `--joblog`:
one row per scheduler/executor lifecycle event,
`(time_ns, job_i, event, description)`,
where `time_ns` is `time.monotonic_ns()` (only differences are meaningful,
not the raw values), `job_i=0` with `event="INIT"` is a synthetic first row
recording the `--jobs` concurrency limit, and `event` is one of
`CREATED`, `STARTED`, `ENDED`, `COMPLETED`.
`CREATED`/`COMPLETED` bracket a job's lifetime as seen by the scheduler
(including dispatch and completion bookkeeping overhead);
`STARTED`/`ENDED` bracket only the actual execution as seen by the executor.
"""

from __future__ import annotations

import argparse
import csv
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
    joblog_path,
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

# One row of the --joblog CSV: (time_ns, job_i, event, description).
JoblogRow = tuple[int, int, str, str]
# A maximal concurrency interval for one event-pair view:
# (start_ns, end_ns, njob_active, active job_i's).
JoblogInterval = tuple[int, int, int, frozenset[int]]

JOBLOG_INIT_RE = re.compile(r"^maximum concurrent jobs: (?P<njob>\d+)$")


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
    print("(cov % can exceed 100% here: nested calls count toward multiple ancestors)")
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


def read_joblog(joblog_file: Path) -> list[JoblogRow]:
    """Parse a `--joblog` CSV file into `(time_ns, job_i, event, description)` rows."""
    with open(joblog_file, newline="") as fh:
        return [
            (int(row["time_ns"]), int(row["job_i"]), row["event"], row["description"])
            for row in csv.DictReader(fh)
        ]


def build_intervals(
    rows: list[JoblogRow], start_event: str, end_event: str
) -> tuple[list[JoblogInterval], frozenset[int]]:
    """Build maximal concurrency intervals from a matched pair of lifecycle events.

    Walks `rows` filtered to `start_event`/`end_event`, sorted by `time_ns`
    (ties broken by processing `end_event` before `start_event`, so a same-instant
    handoff never registers a spurious one-tick overshoot above the configured maximum).

    Returns
    -------
    intervals
        Consecutive `(start_ns, end_ns, njob_active, active_job_is)` covering
        the time range from the first to the last matching event.
    still_open
        `job_i`'s that reached `start_event` but never `end_event`
        (e.g. an interrupted build); these stay counted as active
        through the end of `intervals`.
    """
    events = sorted(
        (time_ns, 0 if event == end_event else 1, job_i, event)
        for time_ns, job_i, event, _description in rows
        if event in (start_event, end_event)
    )
    intervals: list[JoblogInterval] = []
    active: set[int] = set()
    prev_time_ns: int | None = None
    for time_ns, _order, job_i, event in events:
        if prev_time_ns is not None and time_ns > prev_time_ns:
            intervals.append((prev_time_ns, time_ns, len(active), frozenset(active)))
        if event == start_event:
            active.add(job_i)
        else:
            active.discard(job_i)
        prev_time_ns = time_ns
    return intervals, frozenset(active)


def collect_jobs(rows: list[JoblogRow]) -> tuple[dict[int, dict[str, int]], dict[int, str]]:
    """Group event timestamps and descriptions by `job_i`.

    Returns
    -------
    job_times
        `{job_i: {event: time_ns}}`.
    descriptions
        `{job_i: description}`.
    """
    job_times: dict[int, dict[str, int]] = {}
    descriptions: dict[int, str] = {}
    for time_ns, job_i, event, description in rows:
        job_times.setdefault(job_i, {})[event] = time_ns
        descriptions[job_i] = description
    return job_times, descriptions


def print_dip_table(
    title: str,
    intervals: list[JoblogInterval],
    njob: int,
    t0_ns: int,
    descriptions: dict[int, str],
    top: int,
    min_pct: float | None,
) -> float:
    """Print the ranked table of concurrency dips below `njob` for one view.

    A dip is a maximal interval during which fewer than `njob` jobs are active.
    Dips are ranked by lost job-seconds, `(njob - njob_active)` times the interval
    duration, since a long dip that is only barely below `njob` and a short dip
    that is far below `njob` can represent a comparable loss of throughput.

    Returns
    -------
    total_lost
        Lost job-seconds summed over all dips, not just the printed ones.
    """
    dips = [interval for interval in intervals if interval[2] < njob]
    total_lost = sum(
        (njob - njob_active) * (end_ns - start_ns) / 1e9
        for start_ns, end_ns, njob_active, _active in dips
    )
    print(f"\n-- Top {section_limit(top, min_pct)} concurrency dips ({title}) --")
    print(
        f"{'start [s]':>10} {'dur [s]':>8} {'jobs':>4}/{'max':<4} {'lost [job-s]':>12} "
        f"{'%':>6} {'cov %':>6}  detail"
    )
    dips.sort(key=lambda interval: -(njob - interval[2]) * (interval[1] - interval[0]))
    for (start_ns, end_ns, njob_active, active), running_pct in ranked_rows(
        dips,
        top,
        min_pct,
        lambda interval: (njob - interval[2]) * (interval[1] - interval[0]) / 1e9,
        total_lost,
    ):
        start_s = (start_ns - t0_ns) / 1e9
        dur_s = (end_ns - start_ns) / 1e9
        lost = (njob - njob_active) * dur_s
        if len(active) == 1:
            (job_i,) = active
            detail = f"job {job_i}: {descriptions.get(job_i, '')[:60]}"
        else:
            detail = f"{len(active)} jobs active"
        print(
            f"{start_s:10.3f} {dur_s:8.3f} {njob_active:4d}/{njob:<4d} {lost:12.3f} "
            f"{pct(lost, total_lost):6.1f} {running_pct:6.1f}  {detail}"
        )
    return total_lost


def print_dip_histogram(
    title: str,
    intervals: list[JoblogInterval],
    njob: int,
    top: int,
    min_pct: float | None,
) -> None:
    """Print concurrency dips grouped by active-job depth, ranked by aggregate lost job-seconds.

    `print_dip_table` ranks individual dips, so a handful of large one-off dips
    (e.g. the startup ramp-up) dominate the table and a dip depth that recurs very
    often but briefly each time (e.g. a one-slot dispatch-latency gap on every job
    completion) never appears, even though its total impact can be substantial.
    Grouping by depth surfaces that pattern: a high `count` with a low `avg dur`
    points at routine dispatch overhead rather than a one-off stall.
    """
    counts: dict[int, int] = {}
    durs: dict[int, float] = {}
    losts: dict[int, float] = {}
    for start_ns, end_ns, njob_active, _active in intervals:
        if njob_active < njob:
            dur_s = (end_ns - start_ns) / 1e9
            counts[njob_active] = counts.get(njob_active, 0) + 1
            durs[njob_active] = durs.get(njob_active, 0.0) + dur_s
            losts[njob_active] = losts.get(njob_active, 0.0) + (njob - njob_active) * dur_s
    total_lost = sum(losts.values())
    print(f"\n-- Top {section_limit(top, min_pct)} concurrency dip depths ({title}) --")
    print(
        f"{'jobs':>4}/{'max':<4} {'count':>7} {'tot dur [s]':>11} {'avg dur [ms]':>12} "
        f"{'lost [job-s]':>12} {'%':>6} {'cov %':>6}"
    )
    rows = sorted(losts.items(), key=lambda item: -item[1])
    for (njob_active, lost), running_pct in ranked_rows(
        rows, top, min_pct, lambda item: item[1], total_lost
    ):
        count = counts[njob_active]
        dur = durs[njob_active]
        avg_ms = 1000 * dur / count
        print(
            f"{njob_active:4d}/{njob:<4d} {count:7d} {dur:11.3f} {avg_ms:12.3f} "
            f"{lost:12.3f} {pct(lost, total_lost):6.1f} {running_pct:6.1f}"
        )


def print_slowest_jobs_table(
    job_times: dict[int, dict[str, int]],
    descriptions: dict[int, str],
    top: int,
    min_pct: float | None,
) -> None:
    """Print the jobs with the longest actual execution time (`STARTED` -> `ENDED`)."""
    rows = [
        (job_i, times["STARTED"], times["ENDED"], times.get("CREATED"))
        for job_i, times in job_times.items()
        if "STARTED" in times and "ENDED" in times
    ]
    total_exec = sum((ended - started) / 1e9 for _job_i, started, ended, _created in rows)
    print(f"\n-- Top {section_limit(top, min_pct)} jobs by execution time (STARTED -> ENDED) --")
    print(
        f"{'exec [s]':>10} {'%':>6} {'cov %':>6} {'dispatch [ms]':>13}  {'job_i':>6}  description"
    )
    rows.sort(key=lambda row: -(row[2] - row[1]))
    for (job_i, started, ended, created), running_pct in ranked_rows(
        rows, top, min_pct, lambda row: (row[2] - row[1]) / 1e9, total_exec
    ):
        exec_s = (ended - started) / 1e9
        dispatch_ms = f"{(started - created) / 1e6:13.3f}" if created is not None else f"{'-':>13}"
        description = descriptions.get(job_i, "")[:60]
        print(
            f"{exec_s:10.3f} {pct(exec_s, total_exec):6.1f} {running_pct:6.1f} {dispatch_ms}  "
            f"{job_i:6d}  {description}"
        )


def analyze_joblog(joblog_file: Path, top: int, min_pct: float | None) -> None:
    """Print concurrency-dip and dispatch-latency diagnostics from a `--joblog` CSV file.

    Two independent concurrency views are reported: the scheduler's view
    (`CREATED` -> `COMPLETED`, a job's full lifetime including dispatch and
    completion bookkeeping overhead) and the executor's view (`STARTED` -> `ENDED`,
    the actual execution work). Comparing the two views' idle capacity separates
    scheduling/dispatch overhead from genuine lack of parallelism in the workflow graph.
    """
    print(f"\n=== Job log: {joblog_file} ===")
    rows = read_joblog(joblog_file)
    if not rows or rows[0][1] != 0 or rows[0][2] != "INIT":
        print("Job log is empty or does not start with an INIT record.")
        return

    t0_ns = rows[0][0]
    match = JOBLOG_INIT_RE.match(rows[0][3])
    if match is None:
        print(f"Could not parse the INIT record: {rows[0][3]!r}")
        return
    njob = int(match.group("njob"))

    job_rows = [row for row in rows if row[1] != 0]
    if not job_rows:
        print(f"Maximum concurrent jobs: {njob}")
        print("No jobs were recorded.")
        return

    job_times, descriptions = collect_jobs(job_rows)
    span_s = (max(row[0] for row in job_rows) - t0_ns) / 1e9
    print(f"Maximum concurrent jobs: {njob}")
    print(f"Jobs recorded: {len(job_times)}")
    print(f"Build span: {span_s:.3f} s")

    for title, start_event, end_event in (
        ("scheduler: CREATED -> COMPLETED", "CREATED", "COMPLETED"),
        ("executor: STARTED -> ENDED", "STARTED", "ENDED"),
    ):
        intervals, still_open = build_intervals(job_rows, start_event, end_event)
        if still_open:
            job_is = sorted(still_open)
            shown = ", ".join(str(job_i) for job_i in job_is[:10])
            more = f", + {len(job_is) - 10} more" if len(job_is) > 10 else ""
            print(
                f"\nWarning ({title}): {len(job_is)} job(s) never reached {end_event} "
                f"(job_i: {shown}{more}) -- build interrupted?"
            )
        view_span_ns = intervals[-1][1] - intervals[0][0] if intervals else 0
        view_span_s = view_span_ns / 1e9
        mean_concurrency = (
            sum(
                njob_active * (end_ns - start_ns)
                for start_ns, end_ns, njob_active, _active in intervals
            )
            / view_span_ns
            if view_span_ns
            else 0.0
        )
        print(
            f"\n-- Concurrency summary ({title}) --\n"
            f"Mean concurrency: {mean_concurrency:.2f} / {njob} "
            f"({pct(mean_concurrency, njob):.1f}% saturation) over {view_span_s:.3f} s"
        )
        print_dip_table(title, intervals, njob, t0_ns, descriptions, top, min_pct)
        print_dip_histogram(title, intervals, njob, top, min_pct)

    print_slowest_jobs_table(job_times, descriptions, top, min_pct)


def main() -> None:
    """Parse command-line arguments and run the profile, perf.data, SQL log, and job log
    analyses.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("suffix", nargs="?", default="", help="Suffix appended to file names.")
    add_top_argument(parser, 20)
    args = parser.parse_args()

    prof_file = prof_path(args.suffix)
    sqllog_file = sqllog_path(args.suffix)
    perf_data_file = perf_data_path(args.suffix)
    joblog_file = joblog_path(args.suffix)

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

    if joblog_file.is_file():
        analyze_joblog(joblog_file, args.top, args.min_pct)
    else:
        print(f"Job log not found: {joblog_file}")


if __name__ == "__main__":
    main()
