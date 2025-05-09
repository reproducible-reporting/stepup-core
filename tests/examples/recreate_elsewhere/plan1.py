#!/usr/bin/env python3
from stepup.core.api import runsh, static

static("outer.py", "inner.py")
runsh("./outer.py foo", inp="outer.py")
