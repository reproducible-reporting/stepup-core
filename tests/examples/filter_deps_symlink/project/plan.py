#!/usr/bin/env python3
from stepup.core.api import run, static

static("worker.py", "../pkgs_a/")
run("./worker.py")
