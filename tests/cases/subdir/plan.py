#!/usr/bin/env python
from stepup.core.api import static, plan, copy

static("example.txt")
static("sub/")
static("sub/plan.py")
copy("example.txt", "sub/")
plan("sub/")
