#!/usr/bin/env python3
from stepup.core.api import plan, run, static

static("hop1.txt", "sub/plan.py")
run("cp hop1.txt hop2.txt", shell=True, inp="hop1.txt", out="hop2.txt", optional=True)
plan("./plan.py", workdir="sub")
