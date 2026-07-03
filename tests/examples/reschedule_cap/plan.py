#!/usr/bin/env python3
from stepup.core.api import static, step

static("x.py", "trigger.txt")
step("./x.py", inp=["x.py", "trigger.txt"])
