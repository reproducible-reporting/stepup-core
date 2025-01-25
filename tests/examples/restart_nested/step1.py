#!/usr/bin/env python3
import os

from stepup.core.api import step

with open("inp1.txt") as fh:
    text = fh.read().strip()
if text == "a":
    step("./step2.py", inp=["step2.py", "inp2.txt"], out="out2.txt")
with open("out1.txt", "w") as fh:
    print(f"level={os.getenv('LEVEL')}", file=fh)
