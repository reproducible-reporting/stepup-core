#!/usr/bin/env python3

import time

from path import Path

with open("trigger_sub.txt", "w") as f:
    f.write("triggered")

while not Path("trigger_work2.txt").exists():
    time.sleep(0.2)
