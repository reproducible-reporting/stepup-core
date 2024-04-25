#!/usr/bin/env python
from stepup.core.api import static, script

static("helper.py", "work.py")
script("work.py")
