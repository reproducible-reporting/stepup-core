#!/usr/bin/env python3
from stepup.core.api import runsh, static

static("work.py")
runsh("./work.py", inp="work.py")
