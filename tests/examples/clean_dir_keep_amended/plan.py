#!/usr/bin/env python3
from stepup.core.api import static, step

static("data/", "use.py")
step("./use.py", inp="use.py")
