#!/usr/bin/env python3
from stepup.core.api import copy, static

for inp_path in static("inp*.txt"):
    copy(inp_path, "out" + inp_path[3:])
