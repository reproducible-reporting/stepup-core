#!/usr/bin/env python3
from stepup.core.api import amend

# Silly way of copying a file, just for testing purposes.
amend(inp="inp.txt", out="out.txt")
with open("inp.txt", encoding="utf-8") as fh:
    text = fh.read()
with open("out.txt", "w", encoding="utf-8", newline="\n") as fh:
    fh.write(text)
