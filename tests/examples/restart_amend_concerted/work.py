#!/usr/bin/env python3
from stepup.core.api import amend

amend(inp="out1.txt")

with open("inp2.txt", encoding="utf-8") as fh:
    inp2 = fh.read()
with open("out1.txt", encoding="utf-8") as fh:
    out1 = fh.read()
if inp2[0] != out1[0]:
    raise AssertionError("Inconsistent inputs")
with open("out2.txt", "w", encoding="utf-8", newline="\n") as fh:
    fh.write(inp2 + out1)
