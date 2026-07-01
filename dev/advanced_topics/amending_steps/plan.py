#!/usr/bin/env python3
from stepup.core.api import run, static

static("step.py")
run("./step.py", inp=["step.py", "sources.txt"])
run("echo input.txt > sources.txt", shell=True, out="sources.txt")
run("echo You better read this. > input.txt", shell=True, out="input.txt", optional=True)
