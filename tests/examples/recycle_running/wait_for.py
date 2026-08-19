#!/usr/bin/env python3
"""Wait for the first file to show up, then create the second one."""

import sys
import time

from path import Path

path_wait, path_make = sys.argv[1:]
while not Path(path_wait).exists():
    time.sleep(0.2)
with open(path_make, "w") as f:
    f.write("done")
