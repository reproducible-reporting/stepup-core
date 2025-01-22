#!/usr/bin/env python3
from stepup.core.api import static, step

static("step.py")
step("./step.py", inp="step.py")
