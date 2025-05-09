#!/usr/bin/env python3
from stepup.core.api import copy, runsh, static

static("initial.txt", "expensive.py")
copy("initial.txt", "input.txt")
runsh("./expensive.py", inp=["expensive.py", "input.txt"], out="output.txt", block=True)
copy("output.txt", "final.txt")
