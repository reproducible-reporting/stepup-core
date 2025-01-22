#!/usr/bin/env python3
from stepup.core.api import plan, static

static("inp.txt", "foo/", "foo/inp.txt", "bar/", "bar/plan.py")
plan("bar/")
