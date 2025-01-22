#!/usr/bin/env python3
from stepup.core.api import plan, static

static("sub/", "sub/plan.py", "sub/inp.txt")
plan("sub/", inp="inp.txt", out="out.txt")
