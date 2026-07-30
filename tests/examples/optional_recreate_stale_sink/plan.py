#!/usr/bin/env python3
from stepup.core.api import plan, static

static("a/plan.py", "b/plan.py")
plan("./plan.py", workdir="a")
plan("./plan.py", workdir="b")
