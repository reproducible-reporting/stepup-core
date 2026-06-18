#!/usr/bin/env python3
from stepup.core.api import run, static

static("work.py", "helper.py")
run("./work.py 3")
