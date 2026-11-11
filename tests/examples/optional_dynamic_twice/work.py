#!/usr/bin/env python3
from stepup.core.api import amend

with open("work.out", "w", encoding="utf-8", newline="\n") as f:
    f.write("before amend\n")

amend(inp="optional1.txt")
with open("work.out", "a", encoding="utf-8", newline="\n") as f:
    f.write("passed amend optional 1\n")

amend(inp="optional2.txt")
with open("work.out", "a", encoding="utf-8", newline="\n") as f:
    f.write("passed amend optional 2\n")
