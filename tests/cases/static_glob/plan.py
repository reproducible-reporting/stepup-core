#!/usr/bin/env python
from stepup.core.api import glob, copy

for inp_path in glob("inp*.txt"):
    copy(inp_path, "out" + inp_path[3:])
