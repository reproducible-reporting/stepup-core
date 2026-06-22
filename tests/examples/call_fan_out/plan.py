#!/usr/bin/env python3
from stepup.core.api import call, static

static("work.py", "input_a.txt", "input_b.txt", "input_c.txt")
call("./work.py", "plan", planning=True)
