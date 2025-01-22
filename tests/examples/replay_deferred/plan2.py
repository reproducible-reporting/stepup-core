#!/usr/bin/env python3
from stepup.core.api import copy, static

static("work.py")
copy("inp.txt", "out.txt")
