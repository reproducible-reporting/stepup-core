#!/usr/bin/env python3
import time

from path import Path

from stepup.core.api import amend, run

# The work step blocks until this script releases it below.
# The first run creates it, the second one recycles it while it is still running,
# and the third one recycles it after it has completed.
run("./wait_for.py trigger_driver.txt trigger_work.txt")

# Let the plan create gate1.txt and wait for it.
# The first run of this script is deferred here.
with open("go1.txt", "w") as f:
    f.write("go")
amend(inp="gate1.txt")

# Release the work step and wait until its command has finished.
with open("trigger_driver.txt", "w") as f:
    f.write("go")
while not Path("trigger_work.txt").exists():
    time.sleep(0.2)

# The second run of this script is deferred here,
# after the work step has completed but before it is recycled once more.
with open("go2.txt", "w") as f:
    f.write("go")
amend(inp="gate2.txt")
