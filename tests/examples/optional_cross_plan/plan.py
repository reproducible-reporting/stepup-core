#!/usr/bin/env python3
from stepup.core.api import plan, run, static

static("sub/plan.py")
# Optional producer: only runs if (indirectly) needed by a non-optional step.
run("echo hello > out.txt", shell=True, out="out.txt", optional=True)
plan("./plan.py", workdir="sub")
