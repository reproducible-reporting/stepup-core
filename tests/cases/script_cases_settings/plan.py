#!/usr/bin/env python
from stepup.core.api import static, script

static("helper.py", "work.py", "settings.py")
script("work.py")
