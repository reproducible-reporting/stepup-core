#!/usr/bin/env python
from stepup.core.api import static, copy, step

static("initial.txt", "expensive.py")
copy("initial.txt", "input.txt")
step("./expensive.py", inp=["expensive.py", "input.txt"], out="output.txt")
copy("output.txt", "final.txt")
