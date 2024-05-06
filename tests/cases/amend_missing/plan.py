#!/usr/bin/env python
from stepup.core.api import static, step

static("step.py")
step("./step.py", inp="step.py")
step("echo Will be deleted by accident > missing.txt", out="missing.txt")
