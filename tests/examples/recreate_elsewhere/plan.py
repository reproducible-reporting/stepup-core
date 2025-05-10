#!/usr/bin/env python3
from stepup.core.api import runpy, static

static("outer.py", "inner.py")
runpy("./outer.py bar", inp="outer.py")
