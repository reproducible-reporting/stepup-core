#!/usr/bin/env python
from stepup.core.api import static, step

# Not used, intentionally
static("static.txt")
# The following remain pending because the inputs are made static.
step("cp input1.txt output1.txt", inp=["input1.txt"], out=["output1.txt"])
step("cp input2.txt output2.txt", inp=["input2.txt"], out=["output2.txt"])
