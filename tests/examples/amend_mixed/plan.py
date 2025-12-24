#!/usr/bin/env python3
from stepup.core.api import runsh, static

static("work.py")
runsh("./work.py > log.txt", inp="work.py", out="log.txt")
static("inp0.txt", "inp1.txt")
runsh("cp inp1.txt tmp1.txt", inp="inp1.txt", out="tmp1.txt")
runsh("echo Contents of inp2.txt > inp2.txt", out="inp2.txt")
