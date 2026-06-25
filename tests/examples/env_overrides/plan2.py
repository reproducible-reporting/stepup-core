#!/usr/bin/env python3
from stepup.core.api import run, static

static(["show.py"])
# The leading GREETING=world assignment is applied as a step-specific environment
# override. This works even though shell=False (no shell to interpret the assignment).
run("GREETING=world ./show.py", out=["greeting.txt"])
