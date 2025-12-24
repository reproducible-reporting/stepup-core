#!/usr/bin/env python3
from stepup.core.api import amend

amend(inp="inp.txt")
with open("inp.txt") as fh:
    fh.read()
