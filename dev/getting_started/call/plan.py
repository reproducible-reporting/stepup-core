#!/usr/bin/env python3
from stepup.core.api import call, static

static("plot.py", "ebbr.csv", "ebos.csv", "matplotlibrc")
for airport in "ebbr", "ebos":
    call("./plot.py", "plan", planning=True, airport=airport)
