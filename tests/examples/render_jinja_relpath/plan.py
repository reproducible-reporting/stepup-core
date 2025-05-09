#!/usr/bin/env python3
from stepup.core.api import copy, getenv, glob, mkdir, plan, static

PUBLIC = getenv("PUBLIC", back=True)
glob("static/**", _defer=True)
static("variables.py")
mkdir(PUBLIC)
copy("static/preamble.inc.tex", PUBLIC)
plan("static/")
