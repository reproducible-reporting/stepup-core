#!/usr/bin/env python
from stepup.core.api import script, static

static("helper.py", "work.py")
script("work.py")
