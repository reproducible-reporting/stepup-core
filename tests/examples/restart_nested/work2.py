#!/usr/bin/env python3
import os

from stepup.core.api import runsh

runsh("./work3.py", inp=["work3.py", "inp3.txt"], out="out3.txt")
with open("out2.txt", "w") as fh:
    print(f"level={os.getenv('LEVEL')}", file=fh)
