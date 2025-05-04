#!/usr/bin/env python3
from stepup.core.api import runpy, runsh, static

static("work.py")
runpy("./work.py", inp="work.py", out="work.txt")
runsh("echo ping > ping.txt", out="ping.txt")
