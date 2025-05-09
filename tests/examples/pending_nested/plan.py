#!/usr/bin/env python3
from stepup.core.api import glob, runsh

glob("work?.py")
glob("inp?.txt")
runsh("./work1.py", inp=["work1.py", "inp1.txt"], out=["out1.txt"])
