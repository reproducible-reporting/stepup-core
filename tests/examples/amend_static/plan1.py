#!/usr/bin/env python3
from stepup.core.api import runpy, static

static("work.py", "inp1.txt")
runpy("./work.py")
