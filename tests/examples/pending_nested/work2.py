#!/usr/bin/env python3
import os

from stepup.core.api import run

run("./work3.py", inp=["work3.py", "inp3.txt"], out="out3.txt")
with open("out2.txt", "w", encoding="utf-8", newline="\n") as fh:
    print(f"level={os.getenv('LEVEL')}", file=fh)
