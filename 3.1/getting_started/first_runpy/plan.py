#!/usr/bin/env python3
from stepup.core.api import runpy, static

static("work.py", "helper.py")
runpy("./work.py 3")
