#!/usr/bin/env python3
from stepup.core.api import run, static

static("driver.py", "wait_for.py")

# The driver defers itself twice, which is what makes it recycle its work step.
run("./driver.py")

# These two steps wake up the deferred driver by creating the files it waits for.
# They are created here instead of by the driver itself,
# because the steps created by a deferred step are never scheduled:
# a step is only dispatched when all its (recursive) creators are running or have succeeded.
run("./wait_for.py go1.txt gate1.txt", out="gate1.txt")
run("./wait_for.py go2.txt gate2.txt", out="gate2.txt")
