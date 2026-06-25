#!/usr/bin/env python3
from stepup.core.api import run, static

static("work.py", "inp.txt")
run("./work.py", inp="work.py")
