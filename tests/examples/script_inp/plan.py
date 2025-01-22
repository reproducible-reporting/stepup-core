#!/usr/bin/env python3
from stepup.core.api import script, static

static("script.py", "config.txt")
script("script.py", inp="config.txt")
