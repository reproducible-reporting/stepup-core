#!/usr/bin/env python3
import sys

path_out = sys.argv[1]

# This is an evil part of the script to create a corner case.
# Never use this pattern, i.e. changing static files in a script, in production.
if path_out == "case1.txt":
    with open("cases.txt", "w") as fh:
        print("case3.txt", file=fh)
        print("case4.txt", file=fh)

with open(path_out, "w") as fh:
    print("Random text", file=fh)
