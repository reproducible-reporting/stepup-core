#!/usr/bin/env python3
from stepup.core.api import glob, runpy, static

glob("dir_*/", _defer=True)
static("work.py")
runpy("./work.py", inp="work.py")
static("dir_inp/inp.txt")
