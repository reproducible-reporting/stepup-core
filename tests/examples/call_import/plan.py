#!/usr/bin/env python3
from stepup.core.api import call, static

static("helper.py", "work.py")
call("work.py")
