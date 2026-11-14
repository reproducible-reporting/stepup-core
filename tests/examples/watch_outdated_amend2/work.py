#!/usr/bin/env python3
import sys

from stepup.core.api import amend

# The plan redirects the standard output to a file, whose lines must end the same on all platforms.
sys.stdout.reconfigure(newline="\n")

with open(sys.argv[1], encoding="utf-8") as fh:
    path_inp = fh.read().strip()
amend(inp=path_inp)
print(path_inp)
