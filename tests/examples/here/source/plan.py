#!/usr/bin/env python3
from stepup.core.api import getenv, plan, static

static("www/plan.py")
PUBLIC = getenv("PUBLIC", back=True)
plan("./plan.py", workdir="www")
