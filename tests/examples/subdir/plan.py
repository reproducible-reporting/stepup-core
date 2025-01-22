#!/usr/bin/env python3
from stepup.core.api import copy, plan, static

static("example.txt")
static("sub/")
static("sub/plan.py")
copy("example.txt", "sub/")
plan("sub/")
