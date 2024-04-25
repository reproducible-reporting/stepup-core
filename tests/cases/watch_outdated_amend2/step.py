#!/usr/bin/env python
from stepup.core.api import amend
import sys

with open(sys.argv[1]) as fh:
    path_inp = fh.read().strip()
amend(inp=path_inp)
print(path_inp)
