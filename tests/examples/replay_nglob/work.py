#!/usr/bin/env python3
from stepup.core.api import copy, glob

for path in glob("inp.*"):
    copy(path, "out." + path[4:])
