#!/usr/bin/env python
from stepup.core.api import amend, static

static("input.txt")
amend(inp="input.txt", out="output.txt")
with open("output.txt", "w") as fo, open("input.txt") as fi:
    fo.write(fi.read())
