#!/usr/bin/env python3
from stepup.core.api import static, step

static("./original.txt")
step("cp between.txt another.txt", inp=["between.txt"], out=["another.txt"])
step("cp original.txt between.txt", inp=["original.txt"], out=["./between.txt"])
