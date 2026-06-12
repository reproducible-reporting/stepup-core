#!/usr/bin/env python3
from stepup.core.api import copy, getenv, plan, static

PUBLIC = getenv("PUBLIC", back=True)
static("static/", "variables.py")
copy("static/preamble.inc.tex", PUBLIC)
plan("static/")
