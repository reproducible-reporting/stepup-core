#!/usr/bin/env python
from stepup.core.api import static, step

step("cp copy.txt another.txt", inp=["copy.txt"], out=["another.txt"])
step("cp original.txt copy.txt", inp=["original.txt"], out=["./copy.txt"])
static("./original.txt")
