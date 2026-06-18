#!/usr/bin/env python3
from time import sleep

from stepup.core.api import run, static

# The sleep commands are used to ensure the order of executution.
static("work.py")
run("echo hi > f1.txt", shell=True, out="f1.txt")
sleep(0.5)
run("./work.py", inp="work.py", out="f2.txt")
sleep(0.5)
# The following is never executed because work will tamper with f1.txt
run("cat f1.txt f2.txt > f3.txt", shell=True, inp=["f1.txt", "f2.txt"], out="f3.txt")
