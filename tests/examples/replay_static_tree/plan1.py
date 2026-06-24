#!/usr/bin/env python3
from stepup.core.api import copy, run, static

static("work.py")
run("./work.py", inp="work.py")
copy("data/inp.txt", "out.txt")
