#!/usr/bin/env python3
from stepup.core.api import script, static

static("generate.py", "config.json")
script("generate.py")
