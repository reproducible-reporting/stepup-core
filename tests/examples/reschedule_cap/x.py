#!/usr/bin/env python3
from stepup.core.api import amend

# never.txt is never created, so this always reschedules the step.
amend(inp=["never.txt"])
print("never.txt is now available.")
