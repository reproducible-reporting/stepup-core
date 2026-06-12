#!/usr/bin/env python3
from stepup.core.api import call, static

static("worker.py", "../pkgs/")
call("worker.py")
