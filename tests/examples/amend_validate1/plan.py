#!/usr/bin/env python3
from stepup.core.api import runpy, runsh, static

static("work.py")
runsh("echo ping > ping.txt", out="ping.txt")
runsh("echo pong > pong.txt", out="pong.txt")
runpy("./work.py", inp="work.py", out="work.txt")
