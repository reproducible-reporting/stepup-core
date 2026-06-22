#!/usr/bin/env python3
from stepup.core.api import call, static

static("work.py", "config.txt")
call("./work.py", "plan", planning=True, inp=["config.txt"])
