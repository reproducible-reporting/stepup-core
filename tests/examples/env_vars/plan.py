#!/usr/bin/env python3
from stepup.core.api import static, step

static(["variables.json", "demovars.py", "printvars.py"])
step("./demovars.py", inp=["demovars.py", "variables.json"])
