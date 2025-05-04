#!/usr/bin/env python3
from stepup.core.api import runpy, static

static("work1.py", "work2.py")
runpy("./work1.py", inp="work1.py")
runpy("./work2.py", inp="work2.py")
