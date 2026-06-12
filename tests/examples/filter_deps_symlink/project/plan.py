#!/usr/bin/env python3
from stepup.core.api import call, static

static("worker.py", "../pkgs_a/")
call("worker.py")
