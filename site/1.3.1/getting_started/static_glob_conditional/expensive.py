#!/usr/bin/env python

total = 0.0
count = 0
with open("dataset/bigfile.txt") as fh:
    for line in fh:
        total += float(line)
        count += 1

with open("average.txt", "w") as fh:
    print(f"{total / count:f}", file=fh)
