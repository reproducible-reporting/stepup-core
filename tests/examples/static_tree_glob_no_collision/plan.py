#!/usr/bin/env python3
from stepup.core.api import plan, static

static("data/")
static("sub/plan.py")
plan("./plan.py", workdir="sub")
