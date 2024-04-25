#!/usr/bin/env python
from stepup.core.api import script, static

static("generate.py", "config.json")
script("generate.py")
