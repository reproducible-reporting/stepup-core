#!/usr/bin/env python3
from stepup.core.api import runsh, static

static("work1.py", "work2.py")
runsh("./work1.py", inp="work1.py")
runsh("./work2.py", inp="work2.py")
