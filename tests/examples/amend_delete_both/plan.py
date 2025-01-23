#!/usr/bin/env python3
from stepup.core.api import copy, static, step

step("./step.py > log.txt", inp=["step.py", "data1.txt"], out="log.txt")
static("step.py", "asource1.txt", "asource2.txt")
copy("asource1.txt", "data1.txt")
copy("asource2.txt", "data2.txt")
