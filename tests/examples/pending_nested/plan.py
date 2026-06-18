#!/usr/bin/env python3
from stepup.core.api import glob, run

glob("work?.py")
glob("inp?.txt")
run("./work1.py", inp=["work1.py", "inp1.txt"], out=["out1.txt"])
