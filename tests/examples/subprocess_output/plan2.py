#!/usr/bin/env python3
from stepup.core.api import static, step

step("./work.py", inp=["work.py"])
static("work.py")
