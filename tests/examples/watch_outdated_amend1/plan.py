#!/usr/bin/env python3
from stepup.core.api import glob, runsh, static

static("work.py", "subs.txt")
glob("inp*.txt", _defer=True)
runsh("./work.py", inp="subs.txt")
