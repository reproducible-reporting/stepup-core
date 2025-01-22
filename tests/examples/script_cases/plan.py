#!/usr/bin/env python3
from stepup.core.api import script, static

static("helper.py", "work.py")
script("work.py", step_info="current_step_info.json")
