#!/usr/bin/env python3

import time

from path import Path

from stepup.core.api import amend, run

run("./sub.py")

with open("trigger_work1.txt", "w") as f:
    f.write("triggered")
while not Path("trigger_sub.txt").exists():
    time.sleep(0.2)

amend(inp="provided.txt")
with open("provided.txt") as f:
    print(f.read().strip())

with open("trigger_work2.txt", "w") as f:
    f.write("triggered")
