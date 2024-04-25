#!/usr/bin/env python
from stepup.core.api import static, script

static("plot.py", "ebbr.csv", "ebos.csv", "matplotlibrc")
script("plot.py")
