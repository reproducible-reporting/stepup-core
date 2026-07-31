#!/usr/bin/env python3
from stepup.core.api import glob, plan, static

static("data/*.txt")
static("sub/plan.py")

# Two overlapping patterns from this plan both match data/a.txt.
print("MAIN ALL:", sorted(str(p) for p in glob("data/*.txt")))
print("MAIN A:", sorted(str(p) for p in glob("data/a.txt")))

# A completely different plan globs the same static files.
plan("./plan.py", workdir="sub")
