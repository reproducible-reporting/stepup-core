#!/usr/bin/env python3
from stepup.core.api import plan, static

static("sub/plan.py", "sub/inp.txt")
plan("./plan.py", inp="inp.txt", out="out.txt", workdir="sub")
