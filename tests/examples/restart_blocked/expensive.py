#!/usr/bin/env python3
"""Mock script instead of something really slow."""

with open("input.txt") as fh:
    print(fh.read().strip())
with open("output.txt", "w") as fh:
    print("The final output.", file=fh)
