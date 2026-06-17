#!/usr/bin/env python3
from stepup.core.api import plan, runsh, static

static("sub/plan.py")
# Optional producer: only runs if (indirectly) needed by a non-optional step.
runsh("echo hello > out.txt", out="out.txt", optional=True)
plan("sub/")
