#!/usr/bin/env python3
from stepup.core.api import run, static

static("inp1.txt", "work.py")
run("./work.py", inp=["inp1.txt"], out=["out1.txt"])
run("echo word2 > inp2.txt", shell=True, out=["inp2.txt"])
