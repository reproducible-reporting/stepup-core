#!/usr/bin/env python3
from stepup.core.api import copy, plan, static

static("hop1.txt", "sub/", "sub/plan.py")
copy("hop1.txt", "hop2.txt", optional=False)
plan("sub/")
