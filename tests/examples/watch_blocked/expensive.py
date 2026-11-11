#!/usr/bin/env python3
"""Mock script instead of something really slow."""

with open("input.txt", encoding="utf-8") as fh:
    print(fh.read().strip())
with open("output.txt", "w", encoding="utf-8", newline="\n") as fh:
    print("The final output.", file=fh)
