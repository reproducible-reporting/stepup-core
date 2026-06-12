#!/usr/bin/env python3
from stepup.core.api import runpy, static

static("work.py", "subs.txt", "data/")
runpy("./work.py", inp="subs.txt")
