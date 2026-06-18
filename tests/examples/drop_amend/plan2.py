#!/usr/bin/env python3
from stepup.core.api import run, static

# Version 2 of the plan: less sleep time, but no need to rerun work.py
static("work.py")
run("./work.py")
run("sleep 0.5; echo 'Some input for drop_amend example' > inp.txt", shell=True, out="inp.txt")
