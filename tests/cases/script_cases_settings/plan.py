#!/usr/bin/env python
from stepup.core.api import script, static

static("helper.py", "work.py", "settings.py")
script("work.py")
