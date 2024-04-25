#!/usr/bin/env python
from stepup.core.api import static, step

static("input.txt")
step("cp input.txt output.txt", inp=["input.txt"], out=["output.txt"])
