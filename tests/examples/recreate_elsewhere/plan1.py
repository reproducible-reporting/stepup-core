#!/usr/bin/env python3
from stepup.core.api import static, step

static("outer.py", "inner.py")
step("./outer.py foo", inp="outer.py")
