#!/usr/bin/env python3
from stepup.core.api import run, static

static("sub/work.py", "sub/data/a.txt", "b.txt")
run("./work.py", workdir="sub", out="../current_tracked_info.json")
