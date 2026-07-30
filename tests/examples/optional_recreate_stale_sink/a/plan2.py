#!/usr/bin/env python3
from stepup.core.api import run, static

static("hop1.txt")
# Same command (same step label) as in plan1.py, but declared with no inp/out
# paths at all this time -- see README.txt.
run("cp hop1.txt hop2.txt", shell=True, optional=True)
