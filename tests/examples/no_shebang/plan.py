#!/usr/bin/env python3
from stepup.core.api import static, step

static("script.py")
step("./script.py", inp="script.py")
