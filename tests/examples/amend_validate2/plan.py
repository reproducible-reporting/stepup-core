#!/usr/bin/env python3
from stepup.core.api import runsh, static

static("work.py")
runsh("./work.py", inp="work.py", out="work.txt")
runsh("echo ping > ping.txt", out="ping.txt")
