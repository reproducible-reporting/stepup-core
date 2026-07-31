#!/usr/bin/env python3
from stepup.core.api import run, static

# Note that the inputs are not used by the step2.py and step3.py.
# They are just included to easily make some steps pending.
static("inp?.txt")
static("work?.py")

run("./work1.py", inp=["work1.py", "inp1.txt"], out="out1.txt")
