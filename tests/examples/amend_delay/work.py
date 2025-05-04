#!/usr/bin/env python3
from stepup.core.api import amend

# The amend calls are split on purpose to demonstrate the effect of the delay.
# In production code, you would typically call amend with all the files at once.

amend(inp="inp0.txt")
with open("inp0.txt") as fh:
    print(fh.read().strip())

amend(inp="tmp1.txt")
with open("tmp1.txt") as fh:
    print(fh.read().strip())

amend(inp="inp2.txt")
with open("inp2.txt") as fh:
    print(fh.read().strip())
