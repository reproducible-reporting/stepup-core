#!/usr/bin/env python3
from stepup.core.api import runpy, runsh, static

static("work.py")
runsh("echo hi > f1.txt", out="f1.txt")
runpy("./work.py", inp="work.py", out="f2.txt")
# The following is never executed because work will tamper with f1.txt
runsh("cat f1.txt f2.txt > f3.txt", inp=["f1.txt", "f2.txt"], out="f3.txt")
