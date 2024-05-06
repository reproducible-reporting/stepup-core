#!/usr/bin/env python
from stepup.core.api import mkdir, plan, static

static("sub/", "sub/plan.py")
mkdir("../public/")
plan("sub/")
