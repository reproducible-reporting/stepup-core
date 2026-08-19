#!/usr/bin/env python3
import time

from path import Path

with open("trigger_work.txt", "w") as f:
    print("triggered", file=f)

# Bounded sleep (not an infinite loop): gives plan.py's poll loop (every 0.2s) time to
# notice the trigger and raise while this step is still RUNNING, so it gets detached
# instead of finishing first.
time.sleep(2.0)

# A run counter: it must still read 1 after the second run, proving this step was
# skipped rather than executed again.
count = int(Path("out.txt").read_text()) if Path("out.txt").is_file() else 0
Path("out.txt").write_text(str(count + 1))
