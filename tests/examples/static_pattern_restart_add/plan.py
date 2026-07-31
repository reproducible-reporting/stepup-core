#!/usr/bin/env python3
from stepup.core.api import copy, static

for path in static("inp*.txt"):
    copy(path, "out" + path.name[3:])
