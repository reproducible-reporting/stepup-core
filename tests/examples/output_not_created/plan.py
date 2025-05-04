#!/usr/bin/env python3
from stepup.core.api import runsh

runsh("touch input.txt", out=["input.txt"])
runsh("cp input.txt wrong.txt", inp=["input.txt"], out=["output.txt"])
