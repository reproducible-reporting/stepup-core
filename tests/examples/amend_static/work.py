#!/usr/bin/env python3
from stepup.core.api import amend

amend(inp="inp1.txt")
with open("inp1.txt") as fh:
    print(fh.read())

amend(inp="inp2.txt")
with open("inp2.txt") as fh:
    print(fh.read())
