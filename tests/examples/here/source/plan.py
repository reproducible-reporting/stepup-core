#!/usr/bin/env python3
from stepup.core.api import getenv, mkdir, plan, static

static("www/", "www/plan.py")
PUBLIC = getenv("PUBLIC", back=True)
mkdir(PUBLIC)
mkdir(PUBLIC / "www")
plan("www/")
