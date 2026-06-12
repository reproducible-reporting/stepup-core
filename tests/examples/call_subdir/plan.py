#!/usr/bin/env python3
from stepup.core.api import plan, static

static("scripts/repeat.py", "data/plan.py")
plan("data")
