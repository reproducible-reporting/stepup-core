#!/usr/bin/env python3
from stepup.core.api import amend, static

static("inp2.txt")
amend(inp="inp1.txt", out="out1.txt")

with (
    open("inp1.txt", encoding="utf-8", newline="\n") as fr,
    open("out1.txt", "w", encoding="utf-8") as fw,
):
    fw.write(fr.read())
