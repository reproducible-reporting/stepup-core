#!/usr/bin/env python3
from stepup.core.api import glob, runpy

# Note that the inputs are not used by the step2.py and step3.py.
# They are just included to easily make some steps pending.
glob("inp?.txt")
glob("work?.py")

runpy("./work1.py", inp=["work1.py", "inp1.txt"], out="out1.txt")
