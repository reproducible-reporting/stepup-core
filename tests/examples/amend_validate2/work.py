#!/usr/bin/env python3
from stepup.core.api import amend

amend(inp=["ping.txt", "pong.txt"])

# In this example, we never get past the amend.
raise AssertionError("We should not get here.")
