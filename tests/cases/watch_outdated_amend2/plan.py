#!/usr/bin/env python
from stepup.core.api import copy, glob, static, step

static("step.py", "subs1.txt")
for path_inp in glob("inp*.txt"):
    copy(path_inp, "conv" + path_inp[3:])
step("./step.py subs1.txt > subs2.txt", inp="subs1.txt", out="subs2.txt")
step("./step.py subs2.txt > subs3.txt", inp="subs2.txt", out="subs3.txt")
