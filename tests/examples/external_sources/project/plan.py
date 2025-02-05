#!/usr/bin/env python3
from stepup.core.api import call, glob, static

static("worker.py")
glob("../pkgs/**", _defer=True)
call("worker.py")
