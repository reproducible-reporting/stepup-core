#!/usr/bin/env python3
from stepup.core.api import copy, runpy, static

static("work.py")
runpy("./work.py", inp="work.py")
copy("inp.txt", "out.txt")
