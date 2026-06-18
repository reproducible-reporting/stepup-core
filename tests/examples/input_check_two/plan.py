#!/usr/bin/env python3
from stepup.core.api import run, static

static("work.py", "f1.txt")
run("./work.py", inp="work.py", out="f2.txt")
# The following is never executed because work will tamper with f1.txt
run("cat f1.txt f2.txt > f3.txt", shell=True, inp=["f1.txt", "f2.txt"], out="f3.txt")
