#!/usr/bin/env python3
from stepup.core.api import plan, static

static("check.py", "local.txt", "upload.py")
# The planning step compares local.txt with the external data at every start of the director.
plan("./check.py", inp="local.txt", recheck=True)
