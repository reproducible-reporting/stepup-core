#!/usr/bin/env python3
from stepup.core.api import amend, run, static

static("inp.txt")
amend(inp="inp.txt", out="out.txt")
with (
    open("inp.txt", encoding="utf-8", newline="\n") as fh1,
    open("out.txt", "w", encoding="utf-8") as fh2,
):
    fh2.write(fh1.read())
run("echo bye > out1.txt", shell=True, out="out1.txt")
