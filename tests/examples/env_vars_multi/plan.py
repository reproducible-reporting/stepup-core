#!/usr/bin/env python3
from stepup.core.api import plan, static

static("inp.txt", "foo/inp.txt", "bar/plan.py")
plan("./plan.py", workdir="bar")
