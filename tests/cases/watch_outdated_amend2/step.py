#!/usr/bin/env python
import sys

from stepup.core.api import amend

with open(sys.argv[1]) as fh:
    path_inp = fh.read().strip()
amend(inp=path_inp)
print(path_inp)
