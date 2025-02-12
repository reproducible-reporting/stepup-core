#!/usr/bin/env python3
from stepup.core.api import mkdir, plan, static

static("sub/", "sub/plan.py")
mkdir("../public/")
plan("sub/")
