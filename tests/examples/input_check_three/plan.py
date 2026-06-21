#!/usr/bin/env python3

from stepup.core.api import copy, run, static

static("work.py")
run("echo hi > f1.txt", shell=True, out="f1.txt")
copy("f1.txt", "f2.txt")
run("./work.py", inp=["work.py", "f2.txt"], out="f3.txt")
# The following is never executed because work will tamper with f1.txt
run("cat f1.txt f3.txt > f4.txt", shell=True, inp=["f1.txt", "f3.txt"], out="f4.txt")
