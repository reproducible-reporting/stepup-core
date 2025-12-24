#!/usr/bin/env python3
from stepup.core.api import runpy, static

static("work.py", "inp1.txt", "inp2.txt")
runpy("./work.py")
