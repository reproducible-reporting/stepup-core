#!/usr/bin/env python3
from stepup.core.api import run, static

static("getstartup.py")
run("./getstartup.py", out="startup_i.txt")
