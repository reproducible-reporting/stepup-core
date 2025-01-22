#!/usr/bin/env python3
from stepup.core.api import script, static

static("helper.py", "work.py", "settings.py")
script("work.py")
