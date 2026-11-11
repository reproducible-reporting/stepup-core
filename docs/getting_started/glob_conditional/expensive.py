#!/usr/bin/env python3

total = 0.0
count = 0
with open("dataset/bigfile.txt", encoding="utf-8") as fh:
    for line in fh:
        total += float(line)
        count += 1

with open("average.txt", "w", encoding="utf-8", newline="\n") as fh:
    print(f"{total / count:f}", file=fh)
