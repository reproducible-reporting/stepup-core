#!/usr/bin/env python3
from stepup.core.api import runpy, static

static("script.py")
runpy("./script.py", inp="script.py")
