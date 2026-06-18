#!/usr/bin/env python3
from stepup.core.api import run, static

static("work.py")
run("echo ping > ping.txt", shell=True, out="ping.txt")
run("echo pong > pong.txt", shell=True, out="pong.txt")
run("./work.py", inp="work.py", out="work.txt")
