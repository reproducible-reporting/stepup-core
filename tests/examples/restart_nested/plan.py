#!/usr/bin/env python3
from stepup.core.api import glob, step

# Note that the inputs are not used by the step2.py and step3.py.
# They are just included to easily make some steps pending.
glob("inp?.txt")
glob("step?.py")

step("./step1.py", inp=["step1.py", "inp1.txt"], out="out1.txt")
