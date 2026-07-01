#!/usr/bin/env python3
from stepup.core.api import amend, run, shq, static

static("cases.txt", "work.py")
amend(inp="cases.txt")
with open("cases.txt") as fh:
    for line in fh:
        case = line.strip()
        run(f"./work.py {shq(case)}", inp=["work.py", "cases.txt"], out=case)
