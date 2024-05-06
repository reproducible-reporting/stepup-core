#!/usr/bin/env python
from stepup.core.api import getenv, mkdir, plan, static

static("www/", "www/plan.py")
PUBLIC = getenv("PUBLIC", is_path=True)
mkdir(PUBLIC)
mkdir(PUBLIC / "www")
plan("www/")
