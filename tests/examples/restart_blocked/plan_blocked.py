#!/usr/bin/env python3
from stepup.core.api import copy, run, static

static("initial.txt", "expensive.py")
copy("initial.txt", "input.txt")
run("./expensive.py", inp=["expensive.py", "input.txt"], out="output.txt", resources="blocked")
copy("output.txt", "final.txt")
