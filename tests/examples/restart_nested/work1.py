#!/usr/bin/env python3
import os

from stepup.core.api import runpy

with open("inp1.txt") as fh:
    text = fh.read().strip()
if text == "a":
    runpy("./work2.py", inp=["work2.py", "inp2.txt"], out="out2.txt")
with open("out1.txt", "w") as fh:
    print(f"level={os.getenv('LEVEL')}", file=fh)
