#!/usr/bin/env python3
from stepup.core.api import copy, runsh, static

static("work.py")
runsh("./work.py", inp="work.py")
copy("inp.txt", "out.txt")
