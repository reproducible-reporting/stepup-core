#!/usr/bin/env python3
from stepup.core.api import runsh, static

static("./original.txt")
runsh("cp between.txt another.txt", inp=["between.txt"], out=["another.txt"])
runsh("cp original.txt between.txt", inp=["original.txt"], out=["./between.txt"])
