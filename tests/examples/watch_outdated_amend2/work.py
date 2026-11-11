#!/usr/bin/env python3
import sys

from stepup.core.api import amend

with open(sys.argv[1], encoding="utf-8") as fh:
    path_inp = fh.read().strip()
amend(inp=path_inp)
print(path_inp)
