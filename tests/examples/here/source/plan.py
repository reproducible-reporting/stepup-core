#!/usr/bin/env python3
from stepup.core.api import plan, static

static("www/plan.py")
plan("./plan.py", workdir="www")
