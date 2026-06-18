#!/usr/bin/env python3
from stepup.core.api import run, static

static("work.py", "inp1.txt")
run("./work.py")
