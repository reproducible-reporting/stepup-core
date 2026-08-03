#!/usr/bin/env python3
from stepup.core.api import static, step

static("work.py", "trigger.txt")
step("./work.py", inp=["work.py", "trigger.txt"])
