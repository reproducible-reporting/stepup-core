#!/usr/bin/env python3
from stepup.core.api import copy, glob, runsh, static

static("work.py", "subs1.txt")
for path_inp in glob("inp*.txt"):
    copy(path_inp, "conv" + path_inp[3:])
runsh("./work.py subs1.txt > subs2.txt", inp="subs1.txt", out="subs2.txt")
runsh("./work.py subs2.txt > subs3.txt", inp="subs2.txt", out="subs3.txt")
