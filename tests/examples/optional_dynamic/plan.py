#!/usr/bin/env python3
from stepup.core.api import copy, run, static

static("work.py", "data.txt")
copy("data.txt", "optional.txt", optional=True)
run("./work.py", inp="work.py", out="work.out")
