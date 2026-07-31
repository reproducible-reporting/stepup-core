#!/usr/bin/env python3
from stepup.core.api import run, static

static("work.py")
# Every directory matched by the pattern becomes a static tree.
static("data/*/")
run("./work.py", inp="work.py", out="out.txt")
