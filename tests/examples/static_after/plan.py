#!/usr/bin/env python3
import time

from path import Path

from stepup.core.api import copy, static

copy("inp.txt", "out.txt")
static("inp.txt")

# The copy should be made while the plan is still running.
while not Path("out.txt").exists():
    time.sleep(0.2)
