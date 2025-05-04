#!/usr/bin/env python3
from stepup.core.api import copy, runsh, static

runsh("./work.py > log.txt", inp=["work.py", "data1.txt"], out="log.txt")
static("work.py", "asource1.txt", "asource2.txt")
copy("asource1.txt", "data1.txt")
copy("asource2.txt", "data2.txt")
