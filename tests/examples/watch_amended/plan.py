#!/usr/bin/env python3
from stepup.core.api import static, step

static("work.py", "inp.txt")
step("./work.py", inp="work.py")
