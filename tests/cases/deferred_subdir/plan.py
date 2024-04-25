#!/usr/bin/env python
from stepup.core.api import plan, static

static("sub/", "sub/plan.py")
plan("sub/")
