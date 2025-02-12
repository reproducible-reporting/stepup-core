#!/usr/bin/env python3
from stepup.core.api import amend, static, step

static("cases.txt", "work.py")
amend(inp="cases.txt")
with open("cases.txt") as fh:
    for line in fh:
        case = line.strip()
        step("./work.py ${out}", inp=["work.py", "cases.txt"], out=case)
