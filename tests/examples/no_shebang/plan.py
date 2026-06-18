#!/usr/bin/env python3
from stepup.core.api import run, static

static("script.py")
run("./script.py", inp="script.py")
