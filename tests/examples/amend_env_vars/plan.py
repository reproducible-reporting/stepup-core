#!/usr/bin/env python3
from stepup.core.api import run, static

static("foo.txt", "work.py")
run("./work.py")
