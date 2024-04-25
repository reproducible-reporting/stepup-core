#!/usr/bin/env python
from stepup.core.api import step

step("touch input.txt", out=["input.txt"])
step("cp input.txt wrong.txt", inp=["input.txt"], out=["output.txt"])
