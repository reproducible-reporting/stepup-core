#!/usr/bin/env python3
from stepup.core.api import glob, runpy, static

static("work.py", "subs.txt")
glob("inp*.txt", _defer=True)
runpy("./work.py", inp="subs.txt")
