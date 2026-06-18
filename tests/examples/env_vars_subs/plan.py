#!/usr/bin/env python3
from stepup.core.api import getenv, plan, static

static("${SUB}/plan.py")
print(getenv("SUB", path=True))
plan("./plan.py", workdir="${SUB}")
