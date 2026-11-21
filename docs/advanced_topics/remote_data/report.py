#!/usr/bin/env python3
"""Summarize the downloaded data."""

with open("data.txt", encoding="utf-8") as fh:
    values = [float(line) for line in fh]
with open("report.txt", "w", encoding="utf-8", newline="\n") as fh:
    print(f"Number of values: {len(values)}", file=fh)
    print(f"Average: {sum(values) / len(values):.2f}", file=fh)
