#!/usr/bin/env python3
from stepup.core.api import copy, runpy, static

static("work.py", "data.txt")
copy("data.txt", "optional.txt", optional=True)
runpy("./work.py", inp="work.py", out="work.out")
