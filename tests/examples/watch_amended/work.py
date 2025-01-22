#!/usr/bin/env python3
from stepup.core.api import amend

# Silly way of copying a file, just for testing purposes.
amend(inp="inp.txt", out="out.txt")
with open("inp.txt") as fh:
    text = fh.read()
with open("out.txt", "w") as fh:
    fh.write(text)
