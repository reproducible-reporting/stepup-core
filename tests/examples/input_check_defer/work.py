#!/usr/bin/env python3
from stepup.core.api import amend

# This is an evil example. Don't do this in production code.
# Tamper with an already-declared, already-hashed input while running.
with open("trigger.txt", "w") as fh:
    print("tampered", file=fh)

# never.txt is never created, so this always defers the step
# (unless the defer cap has been exceeded).
amend(inp=["never.txt"])
print("never.txt is now available.")
