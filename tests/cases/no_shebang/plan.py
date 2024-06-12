#!/usr/bin/env python
from stepup.core.api import static, step

static("script.py")
step("./script.py", inp="script.py")
