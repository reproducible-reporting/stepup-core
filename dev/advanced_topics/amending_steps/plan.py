#!/usr/bin/env python
from stepup.core.api import static, step

static("step.py")
step("./step.py", inp=["step.py", "sources.txt"])
step("echo input.txt > ${out}", out="sources.txt")
step("echo You better read this. > input.txt", out="input.txt", optional=True)
