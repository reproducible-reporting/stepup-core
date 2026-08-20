#!/usr/bin/env python3
from stepup.core.api import plan, run, static

static("subplan.py")
# The consumer of out.txt is declared before the sub-plan that declares the producer of out.txt.
run("cat out.txt > used1.txt", shell=True, inp=["out.txt"], out=["used1.txt"])
plan("./subplan.py", inp=["subplan.py"])
