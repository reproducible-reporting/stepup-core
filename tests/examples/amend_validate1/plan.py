#!/usr/bin/env python3
from stepup.core.api import runsh, static

static("work.py")
runsh("echo ping > ping.txt", out="ping.txt")
runsh("echo pong > pong.txt", out="pong.txt")
runsh("./work.py", inp="work.py", out="work.txt")
