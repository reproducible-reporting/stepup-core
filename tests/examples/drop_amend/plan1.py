#!/usr/bin/env python3
from stepup.core.api import run, static

# Version 1 of the plan.
static("work.py")
run("./work.py")
run("sleep 1; echo 'Some input for drop_amend example' > inp.txt", shell=True, out="inp.txt")
