#!/usr/bin/env python
from stepup.core.api import getenv, plan, static

static("${SUB}/", "${SUB}/plan.py")
print(getenv("SUB", is_path=True))
plan("${SUB}/")
