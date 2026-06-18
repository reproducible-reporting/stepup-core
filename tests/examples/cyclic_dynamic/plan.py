#!/usr/bin/env python3
from stepup.core.api import run, static

static("work1.py", "work2.py")
run("./work1.py", inp="work1.py")
run("./work2.py", inp="work2.py")
