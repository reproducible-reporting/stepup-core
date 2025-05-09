#!/usr/bin/env python3
from stepup.core.api import runsh, static

static("step.py")
runsh("./step.py", inp=["step.py", "sources.txt"])
runsh("echo input.txt > ${out}", out="sources.txt")
runsh("echo You better read this. > input.txt", out="input.txt", optional=True)
