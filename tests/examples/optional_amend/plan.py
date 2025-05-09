#!/usr/bin/env python3
from stepup.core.api import copy, runsh, static

static("work.py", "data.txt")
copy("data.txt", "optional.txt", optional=True)
runsh("./work.py", inp="work.py", out="work.out")
