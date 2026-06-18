#!/usr/bin/env python3
from stepup.core.api import run, static

static("work.py", "subs.txt", "data/")
run("./work.py", inp="subs.txt")
