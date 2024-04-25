#!/usr/bin/env python
from stepup.core.api import amend

with open("subs.txt") as fh:
    path_inp = fh.read().strip()
amend(inp=path_inp, out="copy.txt")
with open(path_inp, "rb") as fh:
    contents = fh.read()
with open("copy.txt", "wb") as fh:
    fh.write(contents)
