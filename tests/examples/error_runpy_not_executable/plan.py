#!/usr/bin/env python3
from stepup.core.api import runpy, static

static("simple.py")
runpy("./simple.py", inp="simple.py")
