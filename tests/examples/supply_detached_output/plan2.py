#!/usr/bin/env python3
from stepup.core.api import plan, run, static

static("subplan.py")
# Only the command of the consumer changed, so it cannot be recycled and is created anew.
run("cat out.txt > used2.txt", shell=True, inp=["out.txt"], out=["used2.txt"])
plan("./subplan.py", inp=["subplan.py"])
