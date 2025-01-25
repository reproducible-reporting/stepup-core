#!/usr/bin/env python3
from stepup.core.api import glob, step

glob("step?.py")
glob("inp?.txt")
step("./step1.py", inp=["step1.py", "inp1.txt"], out=["out1.txt"])
