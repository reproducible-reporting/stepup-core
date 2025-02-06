#!/usr/bin/env python3
from stepup.core.api import glob, static, step

glob("dir_*/", _defer=True)
static("work.py")
step("./work.py", inp="work.py")
static("dir_inp/inp.txt")
