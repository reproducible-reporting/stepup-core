#!/usr/bin/env python3
from stepup.core.api import glob, static, step

static("step.py", "subs.txt")
glob("inp*.txt", _defer=True)
step("./step.py", inp="subs.txt")
