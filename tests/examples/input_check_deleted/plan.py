#!/usr/bin/env python3
from stepup.core.api import copy, runsh, static

static("work.py")
runsh("echo hi > f1.txt", out="f1.txt")
runsh("./work.py", inp=["work.py", "f1.txt"], out="f2.txt")
copy("f2.txt", "f3.txt")
