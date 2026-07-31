#!/usr/bin/env python3
from stepup.core.api import copy, static

for path in static("inp.*"):
    copy(path, "out." + path[4:])
