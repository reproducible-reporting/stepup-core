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

# The amended input must exist on disk, so `api.amend()`'s post-check
# (`_check_inp_paths`) cannot be the reason for the abort below: only the detached
# branch of `DirectorHandler.amend()` may cause it.
Path("extra_input.txt").write_text("extra")

# By now, plan.py has failed and detached this step (see plan.py and README.txt).
# `DirectorHandler.amend()` forces `carry_on = False` for a detached step, so this
# call must raise `InputNotFoundError`, aborting the step before it reaches the
# `late.txt` marker below instead of running to completion.
amend(inp="extra_input.txt")

Path("late.txt").write_text("this must never be written")
