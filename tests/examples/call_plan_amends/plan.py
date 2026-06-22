#!/usr/bin/env python3
from stepup.core.api import call, static

static("work.py", "dir/a.txt", "dir/b.txt")
call("./work.py", "plan", planning=True)
