#!/usr/bin/env python3
from stepup.core.api import copy, runsh, static

static("work.py", "data1.txt", "data2.txt")
copy("data1.txt", "optional1.txt", optional=True)
copy("data2.txt", "optional2.txt", optional=True)
runsh("./work.py", inp="work.py", out="work.out")
