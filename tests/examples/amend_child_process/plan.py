#!/usr/bin/env python3
from stepup.core.api import run, static

static("work.py", "child/child.py", "child/data.txt")
run("./work.py", inp="child/child.py", out="current_all_info.json")
