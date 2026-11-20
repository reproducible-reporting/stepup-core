#!/usr/bin/env python3
from stepup.core.api import run, static

static("getdirector.py")
run("./getdirector.py", out="director_i.txt")
