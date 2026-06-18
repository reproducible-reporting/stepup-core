#!/usr/bin/env python3
from stepup.core.api import run, static

static("work.py")
run("./work.py", inp="work.py", out="work.txt")
run("echo ping > ping.txt", shell=True, out="ping.txt")
