#!/usr/bin/env python3
from stepup.core.api import amend

with open("data1.txt") as fh:
    text = fh.read()

amend(inp="data2.txt")
with open("data2.txt") as fh:
    text += fh.read()

with open("log.txt", "w") as fh:
    fh.write(text)
