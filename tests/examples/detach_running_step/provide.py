#!/usr/bin/env python3

import time

from path import Path

from stepup.core.api import amend

while not Path("trigger_work1.txt").exists():
    time.sleep(0.2)
while not Path("trigger_sub.txt").exists():
    time.sleep(0.2)
amend(out="provided.txt")
with open("provided.txt", "w") as f:
    f.write("provided for work.py")
