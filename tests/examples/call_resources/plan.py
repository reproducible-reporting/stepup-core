#!/usr/bin/env python3
from stepup.core.api import call, static

static("work.py")
call("./work.py", "run", out=["result.txt"], resources="token:1")
