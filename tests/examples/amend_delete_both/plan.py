#!/usr/bin/env python3
from stepup.core.api import copy, run, static

run("./work.py > log.txt", shell=True, inp=["work.py", "data1.txt"], out="log.txt")
static("work.py", "asource1.txt", "asource2.txt")
copy("asource1.txt", "data1.txt")
copy("asource2.txt", "data2.txt")
