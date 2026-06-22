#!/usr/bin/env python3
from stepup.core.api import run, static

static("creator.py")
static("subplan.py")
run("./creator.py")
