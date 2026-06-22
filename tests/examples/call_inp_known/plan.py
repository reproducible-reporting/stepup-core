#!/usr/bin/env python3
from stepup.core.api import call, static

static("work.py", "a.txt", "b.txt")
call("./work.py", "run", inp=["a.txt", "b.txt"], out=["combined.txt"])
