#!/usr/bin/env python3
import time

with open("trigger_work.txt", "w") as f:
    print("triggered", file=f)

# Bounded sleep (not an infinite loop): gives plan.py's poll loop (every 0.2s) time to
# notice the trigger and raise while this step is still RUNNING, so it gets detached
# instead of finishing first. Unlike the old `while True`, this always terminates on
# its own, so the build cannot hang even though StepUp no longer kills detached steps.
time.sleep(2.0)
