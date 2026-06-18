#!/usr/bin/env python3
from stepup.core.api import copy, run, static

static("work.py")
run("echo hi > f1.txt", shell=True, out="f1.txt")
run("./work.py", inp=["work.py", "f1.txt"], out="f2.txt")
copy("f2.txt", "f3.txt")
