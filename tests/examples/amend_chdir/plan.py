#!/usr/bin/env python3
from stepup.core.api import run, static

static("work.py", "sub/child.py", "sub/data.txt")
run("./work.py", inp=["work.py", "sub/child.py"])
