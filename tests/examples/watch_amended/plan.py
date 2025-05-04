#!/usr/bin/env python3
from stepup.core.api import runpy, static

static("work.py", "inp.txt")
runpy("./work.py", inp="work.py")
