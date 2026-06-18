#!/usr/bin/env python3
from stepup.core.api import run, static

static("bad_script.py")
run("./bad_script.py")
