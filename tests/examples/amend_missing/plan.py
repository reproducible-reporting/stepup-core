#!/usr/bin/env python3
from stepup.core.api import runpy, runsh, static

static("work.py")
runpy("./work.py", inp="work.py")
runsh("echo Will be deleted by accident > missing.txt", out="missing.txt")
