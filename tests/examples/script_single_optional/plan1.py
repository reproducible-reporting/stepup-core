#!/usr/bin/env python3
from stepup.core.api import script, static

static("generate.py")
script("generate.py", optional=True)
