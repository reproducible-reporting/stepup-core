#!/usr/bin/env python3
from stepup.core.api import run, static

static("sub/work.py", "sub/utils.py")
run("./work.py", workdir="sub")
