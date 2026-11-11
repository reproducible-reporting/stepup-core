#!/usr/bin/env python3
from stepup.core.api import amend

amend(inp="inp.txt")
with open("inp.txt", encoding="utf-8") as fh:
    fh.read()
