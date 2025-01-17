#!/usr/bin/env python
from stepup.core.api import plan, static

static("sub/", "sub/plan.py", "part1.txt", "sub/part2.txt")
plan("sub/")
