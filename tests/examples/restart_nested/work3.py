#!/usr/bin/env python3
import os

with open("out3.txt", "w", encoding="utf-8", newline="\n") as fh:
    print(f"level={os.getenv('LEVEL')}", file=fh)
