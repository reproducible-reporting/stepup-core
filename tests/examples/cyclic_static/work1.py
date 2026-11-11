#!/usr/bin/env python3
from stepup.core.api import amend, static

static("inp1.txt")
amend(inp="inp2.txt", out="out2.txt")

with (
    open("inp2.txt", encoding="utf-8", newline="\n") as fr,
    open("out2.txt", "w", encoding="utf-8") as fw,
):
    fw.write(fr.read())
