#!/usr/bin/env python3
from stepup.core.api import runsh, static

static("input.txt")
runsh("cp input.txt output.txt", inp=["input.txt"], out=["output.txt"])
