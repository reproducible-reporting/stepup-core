#!/usr/bin/env python3
import time

from path import Path

from stepup.core.api import run, static

static("work.py")
run("./work.py")
while not Path("trigger_work.txt").exists():
    time.sleep(0.2)
raise RuntimeError("The plan intentionally raises an exception.")
