#!/usr/bin/env python3
from stepup.core.api import runpy, runsh, static

static("inp1.txt")
runpy("./work.py", inp=["inp1.txt"], out=["out1.txt"])
runsh("echo word2 > inp2.txt", out=["inp2.txt"])
