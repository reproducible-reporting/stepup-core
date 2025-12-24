#!/usr/bin/env python3
from stepup.core.api import runpy, runsh, static

# Version 1 of the plan.
static("work.py")
runpy("./work.py")
runsh("sleep 1; echo 'Some input for drop_amend example' > inp.txt", out="inp.txt")
