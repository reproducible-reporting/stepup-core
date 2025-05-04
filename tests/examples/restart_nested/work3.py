#!/usr/bin/env python3
import os

with open("out3.txt", "w") as fh:
    print(f"level={os.getenv('LEVEL')}", file=fh)
