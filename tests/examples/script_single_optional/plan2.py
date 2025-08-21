#!/usr/bin/env python3
from stepup.core.api import copy, script, static

static("generate.py")
script("generate.py", optional=True)
copy("runout.txt", "copy.txt")
