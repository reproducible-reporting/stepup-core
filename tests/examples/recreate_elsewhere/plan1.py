#!/usr/bin/env python3
from stepup.core.api import run, static

static("outer.py", "inner.py")
run("./outer.py foo", inp="outer.py")
