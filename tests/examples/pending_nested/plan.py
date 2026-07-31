#!/usr/bin/env python3
from stepup.core.api import run, static

static("work?.py")
static("inp?.txt")
run("./work1.py", inp=["work1.py", "inp1.txt"], out=["out1.txt"])
