#!/usr/bin/env python3
from stepup.core.api import script, static

static("generate.py", "plot.py", "matplotlibrc")
script("generate.py", optional=True)
script("plot.py")
