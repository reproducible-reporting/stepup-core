#!/usr/bin/env python3
from stepup.core.api import amend, runpy, static

static("cases.txt", "work.py")
amend(inp="cases.txt")
with open("cases.txt") as fh:
    for line in fh:
        case = line.strip()
        runpy("./work.py ${out}", inp=["work.py", "cases.txt"], out=case)
