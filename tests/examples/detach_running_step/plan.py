#!/usr/bin/env python3

from stepup.core.api import run, static

static("provide.py", "sub.py", "work.py")
run("./provide.py")
run("./work.py")
