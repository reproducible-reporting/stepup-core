#!/usr/bin/env python3
import time

from path import Path

# Wait for the plan script to notify us with a trigger.
while not Path("trigger_plan.txt").exists():
    time.sleep(0.2)
# Ping back to the plan script that we're about to finish.
with open("trigger_work.txt", "w") as f:
    f.write("triggered")
