#!/usr/bin/env python3
from stepup.core.api import runsh, static

runsh("cp copy.txt another.txt", inp=["copy.txt"], out=["another.txt"])
runsh("cp original.txt copy.txt", inp=["original.txt"], out=["./copy.txt"])
static("./original.txt")
