#!/usr/bin/env python
from stepup.core.api import static, step

static("step.py")
step("./step.py", inp="step.py")
