#!/usr/bin/env python3
from stepup.core.api import plan, static

static("scripts/", "scripts/repeat.py", "data/", "data/plan.py")
plan("data")
