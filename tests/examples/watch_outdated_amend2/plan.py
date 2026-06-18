#!/usr/bin/env python3
from stepup.core.api import copy, glob, run, static

static("work.py", "subs1.txt")
for path_inp in glob("inp*.txt"):
    copy(path_inp, "conv" + path_inp[3:])
run("./work.py subs1.txt > subs2.txt", shell=True, inp="subs1.txt", out="subs2.txt")
run("./work.py subs2.txt > subs3.txt", shell=True, inp="subs2.txt", out="subs3.txt")
