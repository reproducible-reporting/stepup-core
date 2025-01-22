#!/usr/bin/env python3
from stepup.core.api import amend, static, step

static("inp.txt")
amend(inp="inp.txt", out="out.txt")
with open("inp.txt") as fh1, open("out.txt", "w") as fh2:
    fh2.write(fh1.read())
step("echo bye > out1.txt", out="out1.txt")
