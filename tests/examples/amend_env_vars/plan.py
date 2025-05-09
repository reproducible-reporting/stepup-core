#!/usr/bin/env python3
from stepup.core.api import runsh, static

static("foo.txt")
runsh("./work.py")
