#!/usr/bin/env python3
from stepup.core.api import run, static

static(["variables.json", "demovars.py", "printvars.py"])
run("./demovars.py", inp=["demovars.py", "variables.json"])
