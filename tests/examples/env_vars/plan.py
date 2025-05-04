#!/usr/bin/env python3
from stepup.core.api import runpy, static

static(["variables.json", "demovars.py", "printvars.py"])
runpy("./demovars.py", inp=["demovars.py", "variables.json"])
