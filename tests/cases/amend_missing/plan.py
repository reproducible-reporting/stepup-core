#!/usr/bin/env python
from stepup.core.api import step, static

static("step.py")
step("./step.py", inp="step.py")
step("echo Will be deleted by accident > missing.txt", out="missing.txt")
