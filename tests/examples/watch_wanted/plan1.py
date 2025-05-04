#!/usr/bin/env python3
from stepup.core.api import runsh, static

# Not used, intentionally
static("static.txt")
# The following remain pending because the inputs are made static.
runsh("cp input1.txt output1.txt", inp=["input1.txt"], out=["output1.txt"])
runsh("cp input2.txt output2.txt", inp=["input2.txt"], out=["output2.txt"])
