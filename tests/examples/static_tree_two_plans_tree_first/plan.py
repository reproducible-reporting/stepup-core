#!/usr/bin/env python3
from stepup.core.api import plan, static

# The tree is declared here, by this step.
static("data/")
static("sub/plan.py")
plan("./plan.py", workdir="sub")
