#!/usr/bin/env python3
from stepup.core.api import run, static

static("work.py")
run("./work.py", inp="work.py")
run("echo Will be deleted by accident > missing.txt", shell=True, out="missing.txt")
