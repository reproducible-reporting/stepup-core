#!/usr/bin/env python3
from stepup.core.api import copy, static, step

static("work.py", "data.txt")
copy("data.txt", "optional.txt", optional=True)
step("./work.py", inp="work.py", out="work.out")
