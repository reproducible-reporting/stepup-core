#!/usr/bin/env python3
from stepup.core.api import plan, static

static("sub/", "sub/plan.py")
plan("sub/")
