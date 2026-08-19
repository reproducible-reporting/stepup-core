#!/usr/bin/env python3
import time

from path import Path

from stepup.core.api import amend

with open("trigger_work.txt", "w") as f:
    print("triggered", file=f)

# Bounded sleep (not an infinite loop): gives plan.py's poll loop (every 0.2s) time to
# notice the trigger and raise while this step is still RUNNING, so it gets detached
# before the amend() call below runs.
time.sleep(2.0)

# By now, plan.py has failed and detached this step (see plan.py and README.txt).
# The amendment is carried out all the same, so this step is not aborted and reaches
# the late.txt marker below.
amend(inp="extra_input.txt")

Path("late.txt").write_text(Path("extra_input.txt").read_text())
