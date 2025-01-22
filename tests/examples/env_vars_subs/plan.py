#!/usr/bin/env python3
from stepup.core.api import getenv, plan, static

static("${SUB}/", "${SUB}/plan.py")
print(getenv("SUB", path=True))
plan("${SUB}/")
