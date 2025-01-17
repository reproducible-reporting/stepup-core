#!/usr/bin/env python
from stepup.core.api import script, static

static("plot.py", "ebbr.csv", "ebos.csv", "matplotlibrc")
script("plot.py")
