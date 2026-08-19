#!/usr/bin/env python3
import os
import time

from path import Path

from stepup.core.api import get_rpc_client, run, static

static("work.py")

# Decrement a secret counter that StepUp's directory does not track.
with open("counter.txt") as f:
    counter = int(f.read().strip())
counter -= 1
with open("counter.txt", "w") as f:
    f.write(str(counter))

# The work script will recycle a running step when counter > 1
# and recycles a completed step when counter == 1.
run("./work.py")

# The plan will be deferred as long as the counter is positive.
if counter > 0:
    job_i = int(os.getenv("STEPUP_JOB_I"))
    get_rpc_client().call.defer_step(job_i, ["never.txt"])
else:
    # The work script is waiting for this trigger.
    with open("trigger_plan.txt", "w") as f:
        f.write("triggered")
    # And we wait for the work script to notice it and finish.
    while not Path("trigger_work.txt").exists():
        time.sleep(0.2)
