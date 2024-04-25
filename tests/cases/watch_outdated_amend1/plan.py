#!/usr/bin/env python
from stepup.core.api import static, step, glob

static("step.py", "subs.txt")
glob("inp*.txt", _defer=True)
step("./step.py", inp="subs.txt")
