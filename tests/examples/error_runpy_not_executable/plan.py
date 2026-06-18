#!/usr/bin/env python3
from stepup.core.api import run, static

static("simple.py")
run("./simple.py", inp="simple.py")
