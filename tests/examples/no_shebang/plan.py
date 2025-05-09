#!/usr/bin/env python3
from stepup.core.api import runsh, static

static("script.py")
runsh("./script.py", inp="script.py")
