#!/usr/bin/env python3
from stepup.core.api import amend, hold, run

with hold():
    run("echo first > inp1.txt", shell=True, out="inp1.txt")
    # This must raise AmendWhileHoldingError instead of deferring/deadlocking: inp1.txt's
    # producer is held back by this same hold() block, so it can never become available
    # while the hold is open.
    amend(inp="inp1.txt")
