#!/usr/bin/env python3
from stepup.core.api import runsh, static

static(["variables.json", "demovars.py", "printvars.py"])
runsh("./demovars.py", inp=["demovars.py", "variables.json"])
