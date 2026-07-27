#!/usr/bin/env python3
from stepup.core.api import call, copy, static

static("input.txt", "gen.py")
copy("input.txt", "wanted.txt")
copy("input.txt", "other.txt")
call("./gen.py", "plan", planning=True)
