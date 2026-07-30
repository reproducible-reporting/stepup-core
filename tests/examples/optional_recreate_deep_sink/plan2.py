#!/usr/bin/env python3
from stepup.core.api import plan, run, static

static("hop1.txt", "sub/plan.py")
# Same inputs and outputs as in plan1.py, but a different command,
# so the old step node cannot be recycled and a fresh one is created.
run("tr a-z A-Z < hop1.txt > hop2.txt", shell=True, inp="hop1.txt", out="hop2.txt", optional=True)
plan("./plan.py", workdir="sub")
