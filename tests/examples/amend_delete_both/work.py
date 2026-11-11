#!/usr/bin/env python3
from stepup.core.api import amend

with open("data1.txt", encoding="utf-8") as fh:
    text = fh.read()

amend(inp="data2.txt")
with open("data2.txt", encoding="utf-8") as fh:
    text += fh.read()

with open("log.txt", "w", encoding="utf-8", newline="\n") as fh:
    fh.write(text)
