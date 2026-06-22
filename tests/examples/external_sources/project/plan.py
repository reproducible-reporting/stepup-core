#!/usr/bin/env python3
from stepup.core.api import run, static

static("worker.py", "../pkgs/")
run("./worker.py")
