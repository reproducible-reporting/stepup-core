#!/usr/bin/env python3
from stepup.core.api import copy, static, step

static("work.py")
step("./work.py", inp="work.py")
copy("inp.txt", "out.txt")
