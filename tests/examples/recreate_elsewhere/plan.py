#!/usr/bin/env python3
from stepup.core.api import static, step

static("outer.py", "inner.py")
step("./outer.py bar", inp="outer.py")
