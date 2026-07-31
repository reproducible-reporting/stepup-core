#!/usr/bin/env python3
from stepup.core.api import plan, static

# The file is declared here, by this step.
static("data/foo.txt")
static("sub/plan.py")
plan("./plan.py", workdir="sub")
